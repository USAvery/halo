//Apply CEA-PDB function renames (ghidra-groom next1b) from TSV. DRY-run unless "APPLY".
//@category Analysis
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.CodeUnit;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.program.model.listing.Listing;
import ghidra.program.model.symbol.SourceType;
import ghidra.program.model.symbol.Symbol;
import ghidra.program.model.symbol.SymbolTable;
import java.io.BufferedReader;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.PrintWriter;
import java.util.*;

public class CeaApplyRenames extends GhidraScript {
    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 1) {
            println("Usage: CeaApplyRenames <tsv_path> [APPLY]");
            return;
        }
        String tsvPath = args[0];
        boolean apply = args.length >= 2 && "APPLY".equalsIgnoreCase(args[1]);
        String resultPath = "G:\\dev\\halo\\artifacts\\ghidra_groom\\ghidra_rename_result.txt";

        FunctionManager fm = currentProgram.getFunctionManager();
        SymbolTable st = currentProgram.getSymbolTable();
        Listing listing = currentProgram.getListing();

        int renamed = 0, skipMismatch = 0, notFound = 0, collision = 0, alreadyNew = 0;
        List<String> log = new ArrayList<String>();
        log.add("MODE=" + (apply ? "APPLY" : "DRY") + " tsv=" + tsvPath);

        try (BufferedReader br = new BufferedReader(new FileReader(tsvPath))) {
            String line;
            while ((line = br.readLine()) != null) {
                if (line.trim().isEmpty()) continue;
                String[] p = line.split("\t");
                if (p.length < 3) continue;
                String hex = p[0].trim();
                String oldName = p[1].trim();
                String newName = p[2].trim();
                String tier = p.length > 3 ? p[3].trim() : "";
                long a = Long.parseLong(hex, 16);
                Address addr = currentProgram.getAddressFactory()
                        .getDefaultAddressSpace().getAddress(a);
                Function f = fm.getFunctionAt(addr);
                if (f == null) {
                    notFound++;
                    log.add("NOTFOUND " + hex + " expected=" + oldName);
                    continue;
                }
                String cur = f.getName();
                if (cur.equals(newName)) {
                    alreadyNew++;
                    continue;
                }
                if (!cur.equals(oldName)) {
                    skipMismatch++;
                    log.add("MISMATCH " + hex + " ghidra=" + cur + " expected=" + oldName
                            + " target=" + newName);
                    continue;
                }
                // target-name collision: same name already used at a different address
                boolean coll = false;
                for (Symbol s : st.getGlobalSymbols(newName)) {
                    if (!s.getAddress().equals(addr)) { coll = true; break; }
                }
                if (coll) {
                    collision++;
                    log.add("COLLISION " + hex + " " + oldName + " -> " + newName
                            + " (name exists elsewhere)");
                    continue;
                }
                if (apply) {
                    f.setName(newName, SourceType.USER_DEFINED);
                    String plate = "[NAME: " + newName
                            + " — CEA PDB line-containment (" + tier + "), next1b 2026-07-10]";
                    String old = listing.getComment(CodeUnit.PLATE_COMMENT, addr);
                    if (old == null || old.isEmpty()) {
                        listing.setComment(addr, CodeUnit.PLATE_COMMENT, plate);
                    } else if (!old.contains(plate)) {
                        listing.setComment(addr, CodeUnit.PLATE_COMMENT, old + "\n" + plate);
                    }
                }
                renamed++;
            }
        }

        String summary = (apply ? "APPLIED" : "DRYRUN")
                + " renamed=" + renamed + " skipMismatch=" + skipMismatch
                + " collision=" + collision + " alreadyNew=" + alreadyNew
                + " notFound=" + notFound;
        log.add(0, summary);
        println(summary);
        try (PrintWriter pw = new PrintWriter(new FileWriter(resultPath))) {
            for (String s : log) pw.println(s);
        }
        println("result log -> " + resultPath);
    }
}
