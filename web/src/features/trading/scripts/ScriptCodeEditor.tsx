/**
 * The Omnix Scripts code editor (TVP-11.2): CodeMirror 6 with Pine-compatible highlighting, completion and hover from
 * the interpreter's reference, the server's compile problems inline, and the profiler's time per line in a gutter.
 */
import { basicSetup } from 'codemirror';
import { indentWithTab } from '@codemirror/commands';
import { lintGutter, setDiagnostics } from '@codemirror/lint';
import { Compartment, EditorState, Prec } from '@codemirror/state';
import { EditorView, gutter, GutterMarker, hoverTooltip, keymap } from '@codemirror/view';
import { useEffect, useEffectEvent, useRef } from 'react';
import type { Completion } from '@codemirror/autocomplete';
import { nameAt, omnixScriptLanguage, scriptCompletionOptions, scriptCompletionSource, scriptDiagnostics, scriptHoverText } from './scriptLanguage';
import type { ScriptDiagnostic, ScriptReference } from './scriptsApi';

class ProfileMarker extends GutterMarker {
  constructor(readonly text: string, readonly share: number) {
    super();
  }

  override eq(other: ProfileMarker): boolean {
    return other.text === this.text && other.share === this.share;
  }

  override toDOM(): Node {
    const element = document.createElement('span');
    element.textContent = this.text;
    element.className = this.share >= 0.25 ? 'omnix-script-profile-hot' : 'omnix-script-profile-time';
    element.title = `${Math.round(this.share * 100)}% of the run`;
    return element;
  }
}

/** Milliseconds per line, and its share of the run. */
function profileGutter(profile: ReadonlyArray<{ line: number; seconds: number }>) {
  if (profile.length === 0) return [];
  const total = profile.reduce((sum, item) => sum + item.seconds, 0) || 1;
  const byLine = new Map(profile.map((item) => [item.line, item.seconds]));
  return gutter({
    class: 'omnix-script-profile-gutter',
    lineMarker(view, line) {
      const seconds = byLine.get(view.state.doc.lineAt(line.from).number);
      if (seconds === undefined) return null;
      const ms = seconds * 1_000;
      return new ProfileMarker(ms >= 10 ? `${Math.round(ms)} ms` : `${ms.toFixed(1)} ms`, seconds / total);
    },
    initialSpacer: () => new ProfileMarker('000 ms', 0),
  });
}

const theme = EditorView.theme({
  '&': { height: '100%', fontSize: '12px', backgroundColor: 'var(--trading-script-editor-bg, transparent)' },
  '.cm-scroller': { fontFamily: 'ui-monospace, SFMono-Regular, Consolas, "Liberation Mono", monospace', lineHeight: '1.6' },
  '.cm-gutters': { backgroundColor: 'transparent', borderRight: '1px solid color-mix(in srgb, currentColor 12%, transparent)' },
  '.omnix-script-profile-time': { color: 'color-mix(in srgb, currentColor 55%, transparent)', fontSize: '10px', paddingRight: '4px' },
  '.omnix-script-profile-hot': { color: 'var(--c-amber-770)', fontSize: '10px', fontWeight: '700', paddingRight: '4px' },
});

export function ScriptCodeEditor({
  value,
  onChange,
  reference,
  diagnostics,
  profile,
  onSave,
  label = 'Script source',
}: {
  value: string;
  onChange: (value: string) => void;
  reference: ScriptReference | null;
  diagnostics: readonly ScriptDiagnostic[];
  profile: ReadonlyArray<{ line: number; seconds: number }>;
  onSave: () => void;
  label?: string;
}) {
  const host = useRef<HTMLDivElement | null>(null);
  const view = useRef<EditorView | null>(null);
  const profileCompartment = useRef(new Compartment());
  // The editor is created once; these refs give its callbacks the latest props.
  const latest = useRef({ onChange, onSave, reference, completions: [] as Completion[] });
  latest.current.onChange = onChange;
  latest.current.onSave = onSave;
  if (latest.current.reference !== reference) {
    latest.current.reference = reference;
    latest.current.completions = reference ? scriptCompletionOptions(reference) : [];
  }

  // The editor is created once, on the first value and label; value, diagnostics and profile reach it through the
  // effects below.
  const createEditor = useEffectEvent((parent: HTMLElement) => new EditorView({
    parent,
    state: EditorState.create({
      doc: value,
      extensions: [
        basicSetup,
        keymap.of([indentWithTab]),
        Prec.high(keymap.of([{ key: 'Mod-s', preventDefault: true, run: () => { latest.current.onSave(); return true; } }])),
        omnixScriptLanguage,
        omnixScriptLanguage.data.of({ autocomplete: scriptCompletionSource(() => latest.current.completions) }),
        hoverTooltip((hovered, pos) => {
          const reference = latest.current.reference;
          const found = reference ? nameAt(hovered.state.doc, pos) : null;
          const text = found && reference ? scriptHoverText(reference, found.name) : null;
          if (!found || !text) return null;
          return {
            pos: found.from,
            end: found.to,
            above: true,
            create: () => {
              const dom = document.createElement('div');
              dom.className = 'omnix-script-hover';
              dom.textContent = text;
              return { dom };
            },
          };
        }),
        lintGutter(),
        profileCompartment.current.of([]),
        theme,
        EditorView.contentAttributes.of({ 'aria-label': label }),
        EditorView.updateListener.of((update) => {
          if (update.docChanged) latest.current.onChange(update.state.doc.toString());
        }),
      ],
    }),
  }));
  useEffect(() => {
    if (!host.current) return undefined;
    const editor = createEditor(host.current);
    view.current = editor;
    return () => {
      editor.destroy();
      view.current = null;
    };
  }, []);

  // A new document from outside (another script opened, a version restored).
  useEffect(() => {
    const editor = view.current;
    if (!editor || editor.state.doc.toString() === value) return;
    editor.dispatch({ changes: { from: 0, to: editor.state.doc.length, insert: value } });
  }, [value]);

  useEffect(() => {
    const editor = view.current;
    if (!editor) return;
    editor.dispatch(setDiagnostics(editor.state, scriptDiagnostics(editor.state.doc, diagnostics)));
  }, [diagnostics, value]);

  useEffect(() => {
    view.current?.dispatch({ effects: profileCompartment.current.reconfigure(profileGutter(profile)) });
  }, [profile]);

  return <div ref={host} className="trading-script-code" />;
}
