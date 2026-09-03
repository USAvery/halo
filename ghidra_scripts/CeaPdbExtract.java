import ghidra.app.script.GhidraScript;
import ghidra.app.util.bin.format.pdb2.pdbreader.*;
import ghidra.app.util.bin.format.pdb2.pdbreader.symbol.*;
import java.nio.file.*;
import java.util.*;

// Extract per-module (.obj) procedure symbols + source file + line range from a
// PDB using Ghidra's PDB Universal (pdb2) reader. READ-ONLY: does not touch the
// currently-open program. Args: [0]=pdb path (Windows), [1]=out TSV (Windows).
public class CeaPdbExtract extends GhidraScript {

  static class ProcInfo {
    String name; int seg; long off; long len; String kind;
    HashMap<String,long[]> byFile = new HashMap<>(); // file -> {min,max,count}
  }

  AbstractPdb pdb;
  FileChecksumsC13Section fcs;

  String resolveFile(int fileId) {
    if (fcs == null) return "?";
    try {
      C13FileChecksum fc = fcs.getFileChecksumByOffset(fileId);
      if (fc == null) return "?";
      String s = pdb.getNameStringFromOffset((int) fc.getOffsetFilename());
      return s == null ? "?" : s;
    } catch (Exception e) { return "?"; }
  }

  public void run() throws Exception {
    String[] a = getScriptArgs();
    // Paths hardcoded because the CEA PDB path contains spaces/parens that
    // Ghidra's whitespace arg-splitting cannot survive. args override if given.
    String pdbPath = "C:\\Users\\stian\\Downloads\\Halo_1_Combat_Evolved_Anniversary_(Jun_24,_2011)\\en_us\\HCEX_Release.pdb";
    String outPath = "G:\\dev\\halo\\artifacts\\ghidra_groom\\cea_corpus\\cea_procs_raw.tsv";
    if (a.length >= 1 && !a[0].isEmpty()) pdbPath = a[0];
    if (a.length >= 2 && !a[1].isEmpty()) outPath = a[1];

    PdbReaderOptions opts = new PdbReaderOptions();
    pdb = PdbParser.parse(pdbPath, opts, monitor);
    println("PDB parsed, deserializing...");
    pdb.deserialize();
    PdbDebugInfo di = pdb.getDebugInfo();
    if (di == null) { println("NO DEBUGINFO"); return; }

    int num = di.getNumModules();
    println("total modules=" + num);

    StringBuilder sb = new StringBuilder();
    sb.append("module\tproc\tseg\toff\tlen\tsrc_file\tline_start\tline_end\tnlines\tkind\n");

    int haloModules = 0, totalProcs = 0, haloProcs = 0, haloProcsWithLines = 0;

    for (int m = 1; m <= num; m++) {
      monitor.checkCancelled();
      ghidra.app.util.bin.format.pdb2.pdbreader.Module module = di.getModule(m);
      ModuleInformation mi = module.getModuleInformation();
      String modName = mi.getModuleName();
      if (modName == null) modName = "";
      String modLc = modName.toLowerCase().replace('/', '\\');

      // Collect procs (count all modules for totals)
      List<ProcInfo> procs = new ArrayList<>();
      try {
        MsSymbolIterator sit = module.getSymbolIterator();
        while (sit.hasNext()) {
          AbstractMsSymbol s = sit.next();
          if (s instanceof AbstractProcedureStartMsSymbol) {
            AbstractProcedureStartMsSymbol p = (AbstractProcedureStartMsSymbol) s;
            ProcInfo pi = new ProcInfo();
            pi.name = p.getName();
            pi.seg = p.getSegment();
            pi.off = p.getOffset();
            pi.len = p.getProcedureLength();
            pi.kind = (s instanceof AbstractLocalProcedureStartMsSymbol) ? "LPROC" : "GPROC";
            procs.add(pi);
          }
        }
      } catch (Exception e) { /* module has no/short symbols */ }

      totalProcs += procs.size();

      boolean isHalo = modLc.contains("\\halo\\") && modLc.endsWith(".obj");
      if (!isHalo) continue;
      haloModules++;
      haloProcs += procs.size();

      // Segment -> (off -> proc index) for containment lookup
      HashMap<Integer, TreeMap<Long, Integer>> segMap = new HashMap<>();
      for (int i = 0; i < procs.size(); i++) {
        ProcInfo pi = procs.get(i);
        segMap.computeIfAbsent(pi.seg, k -> new TreeMap<>()).put(pi.off, i);
      }

      // FileChecksums section (first/only)
      fcs = null;
      try {
        C13SectionIterator<FileChecksumsC13Section> fit =
            module.getC13SectionFilteredIterator(FileChecksumsC13Section.class);
        if (fit.hasNext()) fcs = fit.next();
      } catch (Exception e) {}

      // Lines sections -> attribute records to containing procs
      try {
        C13SectionIterator<LinesC13Section> lit =
            module.getC13SectionFilteredIterator(LinesC13Section.class);
        while (lit.hasNext()) {
          LinesC13Section ls = lit.next();
          int seg = ls.getSegCon();
          long base = ls.getOffCon();
          TreeMap<Long, Integer> tm = segMap.get(seg);
          if (tm == null) continue;
          for (C13FileRecord frec : ls.getFileRecords()) {
            String fn = resolveFile(frec.getFileId());
            for (C13LineRecord lr : frec.getLineRecords()) {
              if (lr.isSpecialLine()) continue;
              long ln = lr.getLineNumStart();
              if (ln == 0 || ln >= 0xF00000L) continue;
              long abs = base + lr.getOffset();
              Map.Entry<Long, Integer> e = tm.floorEntry(abs);
              if (e == null) continue;
              ProcInfo pi = procs.get(e.getValue());
              if (abs < pi.off || abs >= pi.off + pi.len) continue;
              long[] mm = pi.byFile.get(fn);
              if (mm == null) { mm = new long[]{ln, ln, 0}; pi.byFile.put(fn, mm); }
              if (ln < mm[0]) mm[0] = ln;
              if (ln > mm[1]) mm[1] = ln;
              mm[2]++;
            }
          }
        }
      } catch (Exception e) {}

      // Emit one row per proc
      String modBase = modName;
      int bs = Math.max(modBase.lastIndexOf('\\'), modBase.lastIndexOf('/'));
      if (bs >= 0) modBase = modBase.substring(bs + 1);
      String stem = modBase.toLowerCase();
      if (stem.endsWith(".obj")) stem = stem.substring(0, stem.length() - 4);

      for (ProcInfo pi : procs) {
        String bestFile = "?"; long bs2 = -1, be2 = -1, bn = 0;
        // Prefer the file whose basename == <stem>.c ; else max count
        long maxCount = -1;
        for (Map.Entry<String, long[]> e : pi.byFile.entrySet()) {
          String f = e.getKey();
          long[] mm = e.getValue();
          String fb = f.toLowerCase();
          int fs = Math.max(fb.lastIndexOf('\\'), fb.lastIndexOf('/'));
          if (fs >= 0) fb = fb.substring(fs + 1);
          boolean isPrimary = fb.equals(stem + ".c") || fb.equals(stem + ".cpp");
          if (isPrimary) { bestFile = f; bs2 = mm[0]; be2 = mm[1]; bn = mm[2]; maxCount = Long.MAX_VALUE; }
          else if (mm[2] > maxCount) { maxCount = mm[2]; bestFile = f; bs2 = mm[0]; be2 = mm[1]; bn = mm[2]; }
        }
        if (bn > 0) haloProcsWithLines++;
        sb.append(modName).append('\t')
          .append(pi.name).append('\t')
          .append(pi.seg).append('\t')
          .append(Long.toHexString(pi.off)).append('\t')
          .append(Long.toHexString(pi.len)).append('\t')
          .append(bestFile).append('\t')
          .append(bs2).append('\t')
          .append(be2).append('\t')
          .append(bn).append('\t')
          .append(pi.kind).append('\n');
      }
    }

    Files.write(Paths.get(outPath), sb.toString().getBytes("UTF-8"));
    println("SCRIPT COMPLETED SUCCESSFULLY");
    println("total_modules=" + num + " halo_modules=" + haloModules
        + " total_procs=" + totalProcs + " halo_procs=" + haloProcs
        + " halo_procs_with_lines=" + haloProcsWithLines);
  }
}
