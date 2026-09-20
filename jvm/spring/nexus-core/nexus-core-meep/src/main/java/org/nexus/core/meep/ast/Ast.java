package org.nexus.core.meep.ast;

import java.util.ArrayList;
import java.util.List;

/**
 * Station 0: deterministic line-oriented Markdown AST parser + feature
 * extractor (Python reference: meep.ast_parser / meep.ast_features).
 * Deliberately NOT a full CommonMark implementation — preserves the exact
 * line-oriented behavior of the reference for classification parity.
 */
public final class Ast {
    private Ast() {}
    public record Node(String type, int level, String content, String language, List<Node> children) {}
    public record Document(List<Node> nodes, String rawText) {}
    public record Features(int wordCount, boolean longDocument, boolean hasHeadings, int headingCount,
                           int maxHeadingDepth, boolean hasCodeBlocks, int codeBlockCount,
                           int codeBlockLineCount, boolean hasLists, int listCount,
                           String headingText, String bodyText, String codeText, boolean structural) {}

    public static Document parse(String text) {
        List<Node> nodes = new ArrayList<>();
        List<Node> list = null;
        StringBuilder paragraph = new StringBuilder();
        String[] lines = text.split("\\n", -1);
        for (int i = 0; i < lines.length; i++) {
            String line = lines[i];
            if (line.matches("^\\s*[-*_]{3,}\\s*$")) { flush(nodes, paragraph); list = null; nodes.add(new Node("thematic_break", 0, "", "", List.of())); continue; }
            if (line.startsWith("```")) {
                flush(nodes, paragraph); list = null;
                String language = line.substring(3).trim();
                StringBuilder code = new StringBuilder();
                i++;
                while (i < lines.length && !lines[i].startsWith("```")) { if (code.length() > 0) code.append('\n'); code.append(lines[i++]); }
                nodes.add(new Node("code_block", 0, code.toString(), language, List.of()));
                continue;
            }
            if (line.matches("^#{1,6}\\s.*")) {
                flush(nodes, paragraph); list = null;
                int level = 0; while (level < line.length() && line.charAt(level) == '#') level++;
                nodes.add(new Node("heading", level, line.substring(level).trim(), "", List.of())); continue;
            }
            if (line.startsWith(">")) { flush(nodes, paragraph); list = null; nodes.add(new Node("blockquote", 0, line.substring(1).trim(), "", List.of())); continue; }
            if (line.matches("^\\s*(?:[-*+] |\\d+\\. ).*")) {
                flush(nodes, paragraph);
                if (list == null) { list = new ArrayList<>(); nodes.add(new Node("list", 0, "", "unordered", list)); }
                String content = line.replaceFirst("^\\s*(?:[-*+] |\\d+\\. )", "").trim();
                list.add(new Node("list_item", 0, content, "", List.of())); continue;
            }
            if (line.isBlank()) { flush(nodes, paragraph); list = null; continue; }
            if (paragraph.length() > 0) paragraph.append(' ');
            paragraph.append(line.trim());
        }
        flush(nodes, paragraph);
        return new Document(List.copyOf(nodes), text);
    }

    private static void flush(List<Node> nodes, StringBuilder paragraph) {
        if (paragraph.length() > 0) { nodes.add(new Node("paragraph", 0, paragraph.toString(), "", List.of())); paragraph.setLength(0); }
    }

    public static Features features(Document doc) {
        int headings = 0, maxDepth = 0, codeBlocks = 0, codeLines = 0, lists = 0;
        List<String> headingText = new ArrayList<>(), body = new ArrayList<>(), code = new ArrayList<>();
        for (Node node : doc.nodes()) {
            switch (node.type()) {
                case "heading" -> { headings++; maxDepth = Math.max(maxDepth, node.level()); headingText.add(node.content()); }
                case "code_block" -> { codeBlocks++; codeLines += node.content().split("\\n", -1).length; code.add(node.content()); }
                case "paragraph", "blockquote" -> body.add(node.content());
                case "list" -> { lists++; node.children().forEach(item -> body.add(item.content())); }
                default -> { }
            }
        }
        String h = String.join("\n", headingText), b = String.join(" ", body), c = String.join("\n", code);
        String all = (h + " " + b + " " + c).trim();
        int words = all.isEmpty() ? 0 : all.split("\\s+").length;
        boolean longDoc = words > 75;
        return new Features(words, longDoc, headings > 0, headings, maxDepth, codeBlocks > 0,
                codeBlocks, codeLines, lists > 0, lists, h, b, c,
                headings > 0 || codeBlocks > 0 || lists > 0 || longDoc);
    }
}
