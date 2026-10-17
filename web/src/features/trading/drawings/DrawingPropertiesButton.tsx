// Generic drawing settings (TVP-0.4): a dialog built from the selected
// drawing's tool `propertySchema`, so every tool gets settings without UI code.
//
// Edits are undo-friendly: number and text inputs commit on blur or Enter,
// and every commit of one field in one dialog session joins a single undo step
// (the merge key). Stored values of an unexpected shape are shown as such and
// never overwritten from here.
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react';
import type { DrawingStyle, TradingDrawing } from './drawingCommands';
import { chartPalette } from '../chartPalette';
import { DrawingTemplateSection, DrawingVisibilitySection } from './DrawingSettingsSections';
import type { DrawingVisibility } from './drawingVisibility';
import { clampToField, numberListProperty, propertyValueReadable, recordsProperty } from './tools/properties';
import { drawingPropertiesWithDefaults, drawingToolDefinition } from './tools/registry';
import type { DrawingProperties, DrawingPropertyField, DrawingPropertyRecord, DrawingPropertyValue, DrawingRecordField } from './tools/types';
import './DrawingPropertiesButton.css';

type Commit = (value: DrawingPropertyValue) => void;

function parseNumberList(text: string): number[] {
  return text.split(',').map((item) => item.trim()).filter((item) => item !== '').map(Number).filter(Number.isFinite);
}

/** A text-like input that commits on blur or Enter, not on every keystroke. */
function DeferredInput({ label, type, value, onCommit, step, min, max }: {
  label: string;
  type: 'text' | 'number';
  value: string;
  onCommit: (text: string) => void;
  step?: number | 'any';
  min?: number;
  max?: number;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const commit = () => {
    if (draft !== value) onCommit(draft);
  };
  return (
    <input
      aria-label={label}
      type={type}
      step={step}
      min={min}
      max={max}
      value={draft}
      onChange={(event) => setDraft(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') commit();
      }}
    />
  );
}

function numberCommit(field: { min?: number; max?: number }, commit: (value: number) => void) {
  return (text: string) => {
    const parsed = Number(text);
    if (text.trim() !== '' && Number.isFinite(parsed)) commit(clampToField(parsed, field));
  };
}

function RecordFieldInput({ field, label, record, onChange }: {
  field: DrawingRecordField;
  label: string;
  record: DrawingPropertyRecord;
  onChange: (value: string | number | boolean) => void;
}) {
  const value = record[field.key];
  switch (field.type) {
    case 'boolean':
      return <input aria-label={label} type="checkbox" checked={value === true} onChange={(event) => onChange(event.target.checked)} />;
    case 'number':
      return (
        <DeferredInput
          label={label}
          type="number"
          step={field.step ?? 'any'}
          min={field.min}
          max={field.max}
          value={typeof value === 'number' ? String(value) : ''}
          onCommit={numberCommit(field, onChange)}
        />
      );
    case 'color':
      return <input aria-label={label} type="color" value={typeof value === 'string' && value ? value : chartPalette.cyan} onChange={(event) => onChange(event.target.value)} />;
    case 'text':
      return <DeferredInput label={label} type="text" value={typeof value === 'string' ? value : ''} onCommit={onChange} />;
  }
}

function PropertyInput({ field, value, onCommit }: { field: DrawingPropertyField; value: DrawingPropertyValue; onCommit: Commit }) {
  const { label } = field;
  switch (field.type) {
    case 'boolean':
      return <input aria-label={label} type="checkbox" checked={value === true} onChange={(event) => onCommit(event.target.checked)} />;
    case 'number':
      return <DeferredInput label={label} type="number" step={field.step ?? 'any'} min={field.min} max={field.max} value={String(value)} onCommit={numberCommit(field, onCommit)} />;
    case 'number-list':
      return (
        <DeferredInput
          label={label}
          type="text"
          value={numberListProperty({ value }, 'value', []).join(', ')}
          onCommit={(text) => onCommit(parseNumberList(text).map((item) => clampToField(item, field)))}
        />
      );
    case 'select':
      return (
        <select aria-label={label} value={String(value)} onChange={(event) => onCommit(event.target.value)}>
          {field.options.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
        </select>
      );
    case 'color':
      return <input aria-label={label} type="color" value={typeof value === 'string' && value ? value : chartPalette.cyan} onChange={(event) => onCommit(event.target.value)} />;
    case 'text':
      return <DeferredInput label={label} type="text" value={String(value)} onCommit={onCommit} />;
    case 'records': {
      const records = recordsProperty({ value }, 'value', []);
      return (
        <div className="trading-drawing-properties-records" role="group" aria-label={label}>
          {records.map((record, index) => (
            <div key={index} className="trading-drawing-properties-record">
              {field.fields.map((recordField) => (
                <RecordFieldInput
                  key={recordField.key}
                  field={recordField}
                  label={`${label} ${index + 1} ${recordField.label}`}
                  record={record}
                  onChange={(next) => onCommit(records.map((item, position) => position === index ? { ...item, [recordField.key]: next } : item))}
                />
              ))}
              <button type="button" aria-label={`Remove ${label} ${index + 1}`} onClick={() => onCommit(records.filter((_, position) => position !== index))}>×</button>
            </div>
          ))}
          <button type="button" onClick={() => onCommit([...records, field.newRecord])}>Add</button>
        </div>
      );
    }
  }
}

/**
 * A "Settings" button and dialog for the selected drawing; nothing when its
 * tool has no properties. Render it with `key={drawing.drawingId}` so another
 * selection starts closed.
 */
export function DrawingPropertiesButton({ drawing, onChange, interval, onVisibilityChange, onApplyTemplate }: {
  drawing: TradingDrawing;
  /** `mergeKey`: commits with the same key belong to one undo step. */
  onChange: (properties: DrawingProperties, mergeKey: string) => void;
  /** With it, the dialog has the Visibility section (TVP-3.8); `interval` is the chart's, for "Only this interval". */
  interval?: string;
  onVisibilityChange?: (visibility: DrawingVisibility, mergeKey?: string) => void;
  /** With it, the dialog has the Template section (TVP-3.8). */
  onApplyTemplate?: (style: DrawingStyle, properties: DrawingProperties) => void;
}) {
  const [open, setOpen] = useState(false);
  const session = useId();
  const [sessionCount, setSessionCount] = useState(0);
  const dialogRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (open) dialogRef.current?.querySelector<HTMLElement>('input, select, button')?.focus();
  }, [open]);
  const definition = drawingToolDefinition(drawing.toolType);
  if (!definition || (definition.propertySchema.length === 0 && !onVisibilityChange && !onApplyTemplate)) return null;
  const stored = drawing.properties ?? {};
  const properties = drawingPropertiesWithDefaults(drawing.toolType, stored);
  const close = () => setOpen(false);
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== 'Escape') return;
    event.stopPropagation();
    close();
  };
  return (
    <span className="trading-drawing-properties-anchor">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => {
          if (!open) setSessionCount((count) => count + 1);
          setOpen((value) => !value);
        }}
      >
        Settings
      </button>
      {open ? (
        <div ref={dialogRef} className="trading-drawing-properties" role="dialog" aria-label={`${definition.label} settings`} onKeyDown={onKeyDown}>
          {definition.propertySchema.map((field) => {
            const value = properties[field.key];
            const readable = value !== undefined && propertyValueReadable(field, value);
            const input = readable
              ? <PropertyInput field={field} value={value} onCommit={(next) => onChange({ ...properties, [field.key]: next }, `${session}:${sessionCount}:${field.key}`)} />
              : <span className="trading-drawing-properties-unreadable">Stored value can't be edited here</span>;
            return field.type === 'records' || !readable
              ? <div key={field.key} className="trading-drawing-properties-row"><span>{field.label}</span>{input}</div>
              : <label key={field.key} className="trading-drawing-properties-row"><span>{field.label}</span>{input}</label>;
          })}
          {onVisibilityChange ? <DrawingVisibilitySection visibility={drawing.visibility} interval={interval ?? '1m'} onChange={onVisibilityChange} /> : null}
          {onApplyTemplate ? <DrawingTemplateSection drawing={drawing} onApply={onApplyTemplate} /> : null}
          <button type="button" onClick={close}>Close</button>
        </div>
      ) : null}
    </span>
  );
}
