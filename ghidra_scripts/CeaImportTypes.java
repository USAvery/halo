//Import verified CEA datum/math types from a flat TSV into /halo/cea. Fixed offsets, packing disabled.
//@category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.program.model.data.*;
import java.io.BufferedReader;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.*;

public class CeaImportTypes extends GhidraScript {
    private DataTypeManager dtm;
    private CategoryPath cp;

    private DataType prim(String b) {
        if (b.equals("float")) return new FloatDataType();
        if (b.equals("char")) return new CharDataType();
        if (b.equals("byte")) return new ByteDataType();
        if (b.equals("short")) return new ShortDataType();
        if (b.equals("ushort")) return new UnsignedShortDataType();
        if (b.equals("int")) return new IntegerDataType();
        if (b.equals("uint")) return new UnsignedIntegerDataType();
        if (b.equals("void*")) return dtm.getPointer(VoidDataType.dataType);
        throw new RuntimeException("unknown prim " + b);
    }

    private DataType resolve(String spec) {
        String[] p = spec.split(":");
        if (p[0].equals("P")) return prim(p[1]);
        if (p[0].equals("A")) {
            DataType base = prim(p[1]);
            int cnt = Integer.parseInt(p[2]);
            return new ArrayDataType(base, cnt, base.getLength());
        }
        if (p[0].equals("R")) {
            DataType r = dtm.getDataType(cp, p[1]);
            if (r == null) throw new RuntimeException("missing ref " + p[1]);
            return r;
        }
        if (p[0].equals("B")) {
            int cnt = Integer.parseInt(p[1]);
            return new ArrayDataType(new ByteDataType(), cnt, 1);
        }
        throw new RuntimeException("bad spec " + spec);
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        String tsv = args.length >= 1 ? args[0]
            : "G:\\dev\\halo\\artifacts\\ghidra_groom\\type_corpus\\import_work.tsv";
        String resultPath = "G:\\dev\\halo\\artifacts\\ghidra_groom\\type_corpus\\import_result.txt";
        dtm = currentProgram.getDataTypeManager();

        List<String> log = new ArrayList<String>();
        int created = 0, fields = 0;

        // First pass: read all, group into type blocks
        List<String[]> types = new ArrayList<String[]>(); // {name,size}
        List<List<String[]>> typeFields = new ArrayList<List<String[]>>(); // per type: {offset,name,spec}
        String catStr = "/halo/cea";
        try (BufferedReader br = new BufferedReader(new FileReader(tsv))) {
            String line;
            List<String[]> curF = null;
            while ((line = br.readLine()) != null) {
                if (line.trim().isEmpty()) continue;
                String[] t = line.split("\t");
                if (t[0].equals("CATEGORY")) { catStr = t[1].trim(); }
                else if (t[0].equals("TYPE")) {
                    types.add(new String[]{t[1].trim(), t[2].trim()});
                    curF = new ArrayList<String[]>();
                    typeFields.add(curF);
                } else if (t[0].equals("FIELD")) {
                    curF.add(new String[]{t[1].trim(), t[2].trim(), t[3].trim()});
                }
            }
        }
        cp = new CategoryPath(catStr);
        dtm.createCategory(cp);

        for (int i = 0; i < types.size(); i++) {
            String name = types.get(i)[0];
            int size = Integer.parseInt(types.get(i)[1]);
            StructureDataType s = new StructureDataType(cp, name, size, dtm);
            s.setPackingEnabled(false);
            int nf = 0;
            for (String[] fld : typeFields.get(i)) {
                int off = Integer.parseInt(fld[0]);
                String fname = fld[1];
                DataType dt = resolve(fld[2]);
                try {
                    s.replaceAtOffset(off, dt, dt.getLength(), fname, null);
                    nf++;
                } catch (Exception ex) {
                    log.add("  FIELDFAIL " + name + "@" + off + " " + fname + " : " + ex.getMessage());
                }
            }
            DataType resolved = dtm.addDataType(s, DataTypeConflictHandler.REPLACE_HANDLER);
            created++;
            fields += nf;
            log.add("CREATED " + name + " size=" + size + " fields=" + nf
                    + " actualSize=" + resolved.getLength());
        }

        String summary = "IMPORT category=" + catStr + " types=" + created + " fields=" + fields;
        log.add(0, summary);
        println(summary);
        try (PrintWriter pw = new PrintWriter(new FileWriter(resultPath))) {
            for (String s : log) pw.println(s);
        }
        println("result log -> " + resultPath);
    }
}
