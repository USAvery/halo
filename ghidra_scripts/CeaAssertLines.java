import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import ghidra.program.model.scalar.Scalar;
import ghidra.program.model.mem.MemoryBlock;
import java.nio.file.*;
import java.util.*;

// Stage 2: enumerate display_assert (0x8d9f0) + error (0x8f390) call sites in
// cachebeta.xbe. For each, walk back the contiguous PUSH block and recover
// (file-string, line immediate), attribute to the containing function. READ-ONLY.
public class CeaAssertLines extends GhidraScript {

  String readCStr(long v) {
    if (v < 0x10000L) return null;
    try {
      Address a = toAddr(v);
      MemoryBlock blk = currentProgram.getMemory().getBlock(a);
      if (blk == null) return null;
      byte[] b = new byte[160];
      int n = 0;
      try { n = currentProgram.getMemory().getBytes(a, b); } catch (Exception e) { return null; }
      StringBuilder s = new StringBuilder();
      for (int i = 0; i < n; i++) {
        int c = b[i] & 0xff;
        if (c == 0) break;
        if (c < 0x20 || c > 0x7e) return null; // not clean ascii
        s.append((char) c);
      }
      return s.length() == 0 ? null : s.toString();
    } catch (Exception e) { return null; }
  }

  public void run() throws Exception {
    long[] targets = { 0x8d9f0L, 0x8f390L };
    String[] tnames = { "display_assert", "error" };
    String outPath = "G:\\dev\\halo\\artifacts\\ghidra_groom\\cea_corpus\\our_assert_lines_raw.tsv";
    ReferenceManager rm = currentProgram.getReferenceManager();
    StringBuilder sb = new StringBuilder();
    sb.append("fn_entry\tfn_name\tfile\tline\tcallsite\tassert_kind\n");

    int sites = 0, resolved = 0;
    for (int ti = 0; ti < targets.length; ti++) {
      Address tgt = toAddr(targets[ti]);
      ReferenceIterator it = rm.getReferencesTo(tgt);
      while (it.hasNext()) {
        monitor.checkCancelled();
        Reference r = it.next();
        if (!r.getReferenceType().isCall()) continue;
        Address callAddr = r.getFromAddress();
        sites++;

        // Collect contiguous PUSH block immediately preceding the CALL (source order)
        ArrayList<long[]> pushes = new ArrayList<>(); // {addr, scalar or -1}
        Instruction cur = getInstructionBefore(callAddr);
        int steps = 0;
        while (cur != null && steps < 16) {
          String mn = cur.getMnemonicString();
          if (mn.equals("PUSH")) {
            Scalar sc = cur.getScalar(0);
            long v = (sc != null) ? sc.getUnsignedValue() : -1L;
            pushes.add(0, new long[]{ cur.getAddress().getOffset(), v });
          } else {
            if (!pushes.isEmpty()) break; // contiguous block ended
          }
          cur = getInstructionBefore(cur.getAddress());
          steps++;
        }
        if (pushes.isEmpty()) continue;

        // Find the file push (operand points to a "c:\..." string)
        int fileIdx = -1; String file = null;
        for (int i = 0; i < pushes.size(); i++) {
          long v = pushes.get(i)[1];
          if (v < 0) continue;
          String s = readCStr(v);
          if (s != null) {
            String sl = s.toLowerCase();
            if (sl.startsWith("c:\\") && sl.endsWith(".c")) { fileIdx = i; file = s; break; }
          }
        }
        if (fileIdx < 0) continue; // error() calls / non-standard: no file/line

        // Line = immediate of the push directly before the file push (param_3)
        long line = -1;
        if (fileIdx - 1 >= 0) {
          long lv = pushes.get(fileIdx - 1)[1];
          if (lv >= 0 && lv < 0x100000L) line = lv;
        }
        if (line < 0) continue;

        Function fc = getFunctionContaining(callAddr);
        String fnEntry = (fc != null) ? fc.getEntryPoint().toString() : "?";
        String fnName = (fc != null) ? fc.getName() : "?";
        resolved++;
        sb.append(fnEntry).append('\t')
          .append(fnName).append('\t')
          .append(file).append('\t')
          .append(line).append('\t')
          .append(callAddr.toString()).append('\t')
          .append(tnames[ti]).append('\n');
      }
    }

    Files.write(Paths.get(outPath), sb.toString().getBytes("UTF-8"));
    println("SCRIPT COMPLETED SUCCESSFULLY");
    println("call_sites_scanned=" + sites + " asserts_resolved=" + resolved);
  }
}
