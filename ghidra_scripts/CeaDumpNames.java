import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.symbol.*;
import java.nio.file.*;

// Dump all function entry names in cachebeta.xbe (addr\tname) so the matcher can
// avoid collisions with existing non-FUN_ symbols. READ-ONLY.
public class CeaDumpNames extends GhidraScript {
  public void run() throws Exception {
    String outPath = "G:\\dev\\halo\\artifacts\\ghidra_groom\\cea_corpus\\ghidra_func_names.tsv";
    StringBuilder sb = new StringBuilder();
    FunctionIterator fi = currentProgram.getFunctionManager().getFunctions(true);
    int n = 0, named = 0;
    while (fi.hasNext()) {
      monitor.checkCancelled();
      Function f = fi.next();
      String nm = f.getName();
      sb.append(f.getEntryPoint().toString()).append('\t').append(nm).append('\n');
      n++;
      if (!nm.startsWith("FUN_")) named++;
    }
    Files.write(Paths.get(outPath), sb.toString().getBytes("UTF-8"));
    println("SCRIPT COMPLETED SUCCESSFULLY");
    println("functions=" + n + " non_FUN_named=" + named);
  }
}
