/**
 * Monaco editor wired up to `monaco-graphql` for schema-aware completion.
 *
 * The schema is re-bound every time the parent passes a new `GraphQLSchema`
 * (i.e. when the endpoint switches). We register a single `*.graphql` file
 * uri and always edit through it; `monaco-graphql` maps from `fileMatch` to
 * the schema we hand it.
 *
 * Cmd/Ctrl+Enter fires `onRun` — bound on the model so it's available even
 * when focus is in the editor.
 */
import { useEffect, useRef } from "react";
import Editor, { type Monaco, type OnMount } from "@monaco-editor/react";
import * as monacoEditor from "monaco-editor";
import { initializeMode } from "monaco-graphql/initializeMode";
import type { GraphQLSchema } from "graphql";

interface Props {
  value: string;
  onChange: (next: string) => void;
  onRun: () => void;
  schema: GraphQLSchema | null;
  /** Used as part of the in-memory file URI so each endpoint has its own model. */
  endpointKey: string;
}

export default function QueryEditor({
  value,
  onChange,
  onRun,
  schema,
  endpointKey,
}: Props) {
  // We keep `onRun` in a ref so the keybinding callback stays stable.
  const onRunRef = useRef(onRun);
  onRunRef.current = onRun;

  // Reinitialise monaco-graphql when the schema changes.
  useEffect(() => {
    if (!schema) return;
    try {
      initializeMode({
        schemas: [
          {
            uri: `inmemory://schema-${endpointKey}.graphql`,
            schema,
            fileMatch: ["*"],
          },
        ],
      });
    } catch {
      // initializeMode throws on re-init in some versions; ignore.
    }
  }, [schema, endpointKey]);

  const handleMount: OnMount = (editor, monaco: Monaco) => {
    editor.addCommand(
      monaco.KeyMod.CtrlCmd | monaco.KeyCode.Enter,
      () => onRunRef.current(),
    );
    // Keyboard shortcut hint in the gutter status; harmless if unused.
    monaco.editor.setTheme("vs");
  };

  return (
    <Editor
      height="100%"
      defaultLanguage="graphql"
      language="graphql"
      path={`query-${endpointKey}.graphql`}
      value={value}
      onChange={(v) => onChange(v ?? "")}
      onMount={handleMount}
      options={{
        minimap: { enabled: false },
        fontSize: 13,
        scrollBeyondLastLine: false,
        automaticLayout: true,
        tabSize: 2,
        renderLineHighlight: "line",
        wordWrap: "on",
      }}
    />
  );
}

// Re-export type so callers don't need to import monaco-editor directly.
export type { monacoEditor };
