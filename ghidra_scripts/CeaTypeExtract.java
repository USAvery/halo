import ghidra.app.script.GhidraScript;
import ghidra.app.util.bin.format.pdb2.pdbreader.*;
import ghidra.app.util.bin.format.pdb2.pdbreader.type.*;
import java.math.BigInteger;
import java.nio.file.*;
import java.util.*;

// Extract the full TPI type corpus (structs/classes/unions/enums) from a PDB
// using Ghidra's PDB Universal (pdb2) reader. READ-ONLY: does not touch the
// currently-open program. Emits a JSON array to the out path.
// Args: [0]=pdb path (Windows), [1]=out JSON path (Windows).
public class CeaTypeExtract extends GhidraScript {

  AbstractPdb pdb;
  int typeMin;

  static String esc(String s) {
    if (s == null) return "";
    StringBuilder b = new StringBuilder();
    for (int i = 0; i < s.length(); i++) {
      char c = s.charAt(i);
      switch (c) {
        case '"': b.append("\\\""); break;
        case '\\': b.append("\\\\"); break;
        case '\n': b.append("\\n"); break;
        case '\r': b.append("\\r"); break;
        case '\t': b.append("\\t"); break;
        default:
          if (c < 0x20) b.append(String.format("\\u%04x", (int) c));
          else b.append(c);
      }
    }
    return b.toString();
  }

  AbstractMsType rec(RecordNumber rn) {
    if (rn == null) return null;
    try { return pdb.getTypeRecord(rn); } catch (Exception e) { return null; }
  }

  // Resolve a type record-number to a readable C-ish type string.
  String resolve(RecordNumber rn, int depth) {
    if (rn == null) return "void";
    int idx;
    try { idx = rn.getNumber(); } catch (Exception e) { return "unknown"; }
    if (idx < 0) return "void";
    if (depth > 8) return "type#" + idx;
    AbstractMsType t = rec(rn);
    if (t == null) return "type#" + idx;
    try {
      if (t instanceof PrimitiveMsType) {
        String n = t.getName();
        return (n == null || n.isEmpty()) ? "prim#" + idx : n;
      }
      if (t instanceof AbstractPointerMsType) {
        AbstractPointerMsType p = (AbstractPointerMsType) t;
        return resolve(p.getUnderlyingRecordNumber(), depth + 1) + " *";
      }
      if (t instanceof AbstractModifierMsType) {
        AbstractModifierMsType m = (AbstractModifierMsType) t;
        return resolve(m.getModifiedRecordNumber(), depth + 1);
      }
      if (t instanceof AbstractArrayMsType) {
        AbstractArrayMsType a = (AbstractArrayMsType) t;
        String elem = resolve(a.getElementTypeRecordNumber(), depth + 1);
        BigInteger sz = null;
        try { sz = a.getSize(); } catch (Exception e) {}
        return elem + " array[bytes=" + (sz == null ? "?" : sz.toString()) + "]";
      }
      if (t instanceof AbstractBitfieldMsType) {
        AbstractBitfieldMsType bf = (AbstractBitfieldMsType) t;
        String base = resolve(bf.getElementRecordNumber(), depth + 1);
        return base + " : " + bf.getBitLength();
      }
      if (t instanceof AbstractCompositeMsType) {
        String n = t.getName();
        return (n == null || n.isEmpty()) ? "anon_composite#" + idx : n;
      }
      if (t instanceof AbstractEnumMsType) {
        String n = t.getName();
        return (n == null || n.isEmpty()) ? "anon_enum#" + idx : "enum " + n;
      }
      if (t instanceof AbstractProcedureMsType || t instanceof AbstractMemberFunctionMsType) {
        return "funcptr";
      }
      String n = t.getName();
      if (n != null && !n.isEmpty()) return n;
      return t.getClass().getSimpleName() + "#" + idx;
    } catch (Exception e) {
      return "type#" + idx;
    }
  }

  boolean isBitfield(RecordNumber rn) {
    AbstractMsType t = rec(rn);
    return t instanceof AbstractBitfieldMsType;
  }

  public void run() throws Exception {
    String[] a = getScriptArgs();
    String pdbPath = "C:\\path\\to\\Halo_1_Combat_Evolved_Anniversary_(Jun_24,_2011)\\en_us\\HCEX.pdb";
    String outPath = "G:\\dev\\halo\\artifacts\\ghidra_groom\\type_corpus\\cea_debug_types_raw.json";
    if (a.length >= 1 && !a[0].isEmpty()) pdbPath = a[0];
    if (a.length >= 2 && !a[1].isEmpty()) outPath = a[1];

    PdbReaderOptions opts = new PdbReaderOptions();
    pdb = PdbParser.parse(pdbPath, opts, monitor);
    println("PDB parsed, deserializing...");
    pdb.deserialize();

    TypeProgramInterface tpi = pdb.getTypeProgramInterface();
    if (tpi == null) { println("NO TPI"); return; }
    typeMin = tpi.getTypeIndexMin();
    int maxEx = tpi.getTypeIndexMaxExclusive();
    println("TPI range " + typeMin + " .. " + maxEx);

    StringBuilder sb = new StringBuilder();
    sb.append("[\n");
    boolean first = true;
    int nStruct = 0, nUnion = 0, nEnum = 0, nFwd = 0, nFail = 0, nAnon = 0;

    for (int i = typeMin; i < maxEx; i++) {
      if ((i & 0x3ff) == 0) monitor.checkCancelled();
      RecordNumber rn = RecordNumber.typeRecordNumber(i);
      AbstractMsType t = rec(rn);
      if (t == null) continue;

      try {
        // ---- Composite (struct / class / union) ----
        if (t instanceof AbstractCompositeMsType) {
          AbstractCompositeMsType c = (AbstractCompositeMsType) t;
          boolean fwd = false;
          try { fwd = c.getMsProperty().isForwardReference(); } catch (Exception e) {}
          if (fwd) { nFwd++; continue; }
          String name = c.getName();
          if (name == null) name = "";
          boolean anon = name.isEmpty() || name.startsWith("<unnamed") || name.startsWith("__unnamed");
          if (anon) nAnon++;
          String kind = (c instanceof AbstractUnionMsType) ? "union" : "struct";
          BigInteger sz = BigInteger.ZERO;
          try { sz = c.getSize(); } catch (Exception e) {}

          // field list
          List<Object[]> fields = new ArrayList<>(); // {name, offsetStr, typeStr, bitfieldStr|null}
          RecordNumber flRn = null;
          try { flRn = c.getFieldDescriptorListRecordNumber(); } catch (Exception e) {}
          AbstractMsType flt = rec(flRn);
          if (flt instanceof AbstractFieldListMsType) {
            AbstractFieldListMsType fl = (AbstractFieldListMsType) flt;
            List<AbstractMemberMsType> members;
            try { members = fl.getNonStaticMembers(); } catch (Exception e) { members = Collections.emptyList(); }
            for (AbstractMemberMsType mem : members) {
              String fn = mem.getName();
              BigInteger off = BigInteger.ZERO;
              try { off = mem.getOffset(); } catch (Exception e) {}
              RecordNumber ftrn = null;
              try { ftrn = mem.getFieldTypeRecordNumber(); } catch (Exception e) {}
              String tstr = resolve(ftrn, 0);
              String bf = null;
              if (isBitfield(ftrn)) {
                AbstractBitfieldMsType b = (AbstractBitfieldMsType) rec(ftrn);
                bf = b.getBitPosition() + ":" + b.getBitLength();
              }
              fields.add(new Object[]{ fn, off.toString(), tstr, bf });
            }
          }

          if (kind.equals("union")) nUnion++; else nStruct++;

          if (!first) sb.append(",\n"); first = false;
          sb.append("{\"name\":\"").append(esc(name.isEmpty() ? ("anon#" + i) : name)).append("\",")
            .append("\"kind\":\"").append(kind).append("\",")
            .append("\"anon\":").append(anon ? "true" : "false").append(",")
            .append("\"size\":").append(sz.toString()).append(",")
            .append("\"type_index\":").append(i).append(",")
            .append("\"fields\":[");
          for (int k = 0; k < fields.size(); k++) {
            Object[] f = fields.get(k);
            if (k > 0) sb.append(",");
            sb.append("{\"name\":\"").append(esc((String) f[0])).append("\",")
              .append("\"offset\":").append((String) f[1]).append(",")
              .append("\"type\":\"").append(esc((String) f[2])).append("\"");
            if (f[3] != null) sb.append(",\"bitfield\":\"").append(esc((String) f[3])).append("\"");
            sb.append("}");
          }
          sb.append("]}");
        }
        // ---- Enum ----
        else if (t instanceof AbstractEnumMsType) {
          AbstractEnumMsType en = (AbstractEnumMsType) t;
          boolean fwd = false;
          try { fwd = en.getMsProperty().isForwardReference(); } catch (Exception e) {}
          if (fwd) { nFwd++; continue; }
          String name = en.getName();
          if (name == null) name = "";
          boolean anon = name.isEmpty() || name.startsWith("<unnamed") || name.startsWith("__unnamed");
          if (anon) nAnon++;
          String underlying = "int";
          try { underlying = resolve(en.getUnderlyingRecordNumber(), 0); } catch (Exception e) {}

          List<String[]> members = new ArrayList<>();
          RecordNumber flRn = null;
          try { flRn = en.getFieldDescriptorListRecordNumber(); } catch (Exception e) {}
          AbstractMsType flt = rec(flRn);
          if (flt instanceof AbstractFieldListMsType) {
            AbstractFieldListMsType fl = (AbstractFieldListMsType) flt;
            List<AbstractEnumerateMsType> ml;
            try { ml = fl.getEnumerates(); } catch (Exception e) { ml = Collections.emptyList(); }
            for (AbstractEnumerateMsType em : ml) {
              String vn = em.getName();
              String vv;
              try { vv = em.getNumeric().getIntegral().toString(); } catch (Exception e) {
                try { vv = em.getNumeric().toString(); } catch (Exception e2) { vv = "?"; }
              }
              members.add(new String[]{ vn, vv });
            }
          }
          nEnum++;
          if (!first) sb.append(",\n"); first = false;
          sb.append("{\"name\":\"").append(esc(name.isEmpty() ? ("anon_enum#" + i) : name)).append("\",")
            .append("\"kind\":\"enum\",")
            .append("\"anon\":").append(anon ? "true" : "false").append(",")
            .append("\"underlying\":\"").append(esc(underlying)).append("\",")
            .append("\"type_index\":").append(i).append(",")
            .append("\"members\":[");
          for (int k = 0; k < members.size(); k++) {
            String[] mv = members.get(k);
            if (k > 0) sb.append(",");
            sb.append("{\"name\":\"").append(esc(mv[0])).append("\",\"value\":\"").append(esc(mv[1])).append("\"}");
          }
          sb.append("]}");
        }
      } catch (Exception e) {
        nFail++;
      }
    }

    sb.append("\n]\n");
    Files.write(Paths.get(outPath), sb.toString().getBytes("UTF-8"));
    println("SCRIPT COMPLETED SUCCESSFULLY");
    println("structs=" + nStruct + " unions=" + nUnion + " enums=" + nEnum
        + " forward_refs_skipped=" + nFwd + " anon=" + nAnon + " parse_fail=" + nFail);
  }
}
