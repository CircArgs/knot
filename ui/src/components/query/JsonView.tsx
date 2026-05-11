/**
 * Read-only JSON viewer. Reuses Monaco since it's already loaded for the
 * editor — no point shipping a second syntax highlighter.
 */
import Editor from "@monaco-editor/react";

interface Props {
  value: unknown;
}

export default function JsonView({ value }: Props) {
  const text = JSON.stringify(value, null, 2);
  return (
    <Editor
      height="100%"
      defaultLanguage="json"
      language="json"
      value={text}
      options={{
        readOnly: true,
        minimap: { enabled: false },
        fontSize: 12,
        scrollBeyondLastLine: false,
        automaticLayout: true,
        wordWrap: "on",
        lineNumbers: "off",
        folding: true,
      }}
    />
  );
}
