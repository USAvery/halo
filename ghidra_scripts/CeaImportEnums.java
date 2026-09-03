//Import CEA game enums from a flat TSV into /halo/cea. Skips (never clobbers) name collisions.
//@category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.program.model.data.*;
import java.io.BufferedReader;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.*;

public class CeaImportEnums extends GhidraScript {
    private DataTypeManager dtm;
    private CategoryPath cp;

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        String tsv = args.length >= 1 ? args[0]
            : "G:\\dev\\halo\\artifacts\\ghidra_groom\\type_corpus\\enum_import_work.tsv";
        String resultPath = "G:\\dev\\halo\\artifacts\\ghidra_groom\\type_corpus\\enum_import_result.txt";
        dtm = currentProgram.getDataTypeManager();

        List<String> log = new ArrayList<String>();
        int created = 0, skipped = 0, members = 0;
        String catStr = "/halo/cea";

        // Parse TSV into enum blocks
        List<String[]> enums = new ArrayList<String[]>();          // {name,size}
        List<List<String[]>> enumMembers = new ArrayList<List<String[]>>(); // per enum: {name,value}
        try (BufferedReader br = new BufferedReader(new FileReader(tsv))) {
            String line;
            List<String[]> cur = null;
            while ((line = br.readLine()) != null) {
                if (line.trim().isEmpty()) continue;
                String[] t = line.split("\t");
                if (t[0].equals("CATEGORY")) { catStr = t[1].trim(); }
                else if (t[0].equals("ENUM")) {
                    enums.add(new String[]{t[1].trim(), t[2].trim()});
                    cur = new ArrayList<String[]>();
                    enumMembers.add(cur);
                } else if (t[0].equals("MEMBER")) {
                    cur.add(new String[]{t[1].trim(), t[2].trim()});
                }
            }
        }
        cp = new CategoryPath(catStr);
        dtm.createCategory(cp);

        for (int i = 0; i < enums.size(); i++) {
            String name = enums.get(i)[0];
            int size = Integer.parseInt(enums.get(i)[1]);

            // Collision check: skip if a type with this name exists ANYWHERE (never clobber)
            ArrayList<DataType> hits = new ArrayList<DataType>();
            dtm.findDataTypes(name, hits);
            if (!hits.isEmpty()) {
                String where = hits.get(0).getCategoryPath().getPath();
                log.add("SKIP-COLLISION " + name + " (exists at " + where + ")");
                skipped++;
                continue;
            }

            EnumDataType e = new EnumDataType(cp, name, size, dtm);
            int nm = 0;
            for (String[] mem : enumMembers.get(i)) {
                long val = Long.parseLong(mem[1]);
                try {
                    e.add(mem[0], val);
                    nm++;
                } catch (Exception ex) {
                    log.add("  MEMBERFAIL " + name + "." + mem[0] + "=" + val + " : " + ex.getMessage());
                }
            }
            dtm.addDataType(e, DataTypeConflictHandler.DEFAULT_HANDLER);
            created++;
            members += nm;
            log.add("CREATED " + name + " size=" + size + " members=" + nm);
        }

        String summary = "IMPORT-ENUMS category=" + catStr + " created=" + created
                + " skipped=" + skipped + " members=" + members;
        log.add(0, summary);
        println(summary);
        try (PrintWriter pw = new PrintWriter(new FileWriter(resultPath))) {
            for (String s : log) pw.println(s);
        }
        println("result log -> " + resultPath);
    }
}
