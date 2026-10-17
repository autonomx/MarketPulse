import { useState, type ReactNode } from 'react';
import { nextDrawingGroupName, selectedDrawingIds, type TradingDrawing } from './drawings/drawingCommands';
import { drawingDisplayName } from './drawings/tools/registry';
import type { useTradingDrawings } from './drawings/useTradingDrawings';

type Drawings = ReturnType<typeof useTradingDrawings>;
type Renaming = { kind: 'drawing' | 'group'; id: string } | null;
const DRAG_TYPE = 'application/x-omnix-drawing';

/** The name the tree shows: the drawing's own, else its tool's. */
export function drawingTreeName(drawing: TradingDrawing): string {
  return drawing.name || drawingDisplayName(drawing);
}

/** Top-most first, groups where their top-most drawing is: [group or null, drawings][]. */
function treeRows(drawings: readonly TradingDrawing[]): Array<[string | null, TradingDrawing[]]> {
  const rows: Array<[string | null, TradingDrawing[]]> = [];
  const groups = new Map<string, TradingDrawing[]>();
  for (const drawing of [...drawings].reverse()) {
    if (!drawing.group) {
      rows.push([null, [drawing]]);
      continue;
    }
    const members = groups.get(drawing.group);
    if (members) members.push(drawing);
    else {
      const list = [drawing];
      groups.set(drawing.group, list);
      rows.push([drawing.group, list]);
    }
  }
  return rows;
}

function NameInput({ value, label, onDone }: { value: string; label: string; onDone: (name: string | null) => void }) {
  const [text, setText] = useState(value);
  return (
    <input
      className="trading-object-rename"
      aria-label={label}
      autoFocus
      value={text}
      maxLength={80}
      onChange={(event) => setText(event.target.value)}
      onBlur={() => onDone(text)}
      onKeyDown={(event) => {
        event.stopPropagation();
        if (event.key === 'Enter') onDone(text);
        if (event.key === 'Escape') onDone(null);
      }}
    />
  );
}

/**
 * The chart's drawings in the object tree (TVP-3.8): top-most first, in named groups; rename (double-click or the
 * pencil), move up or down a step, drag to another drawing's place (and group), group the selected drawings, and show,
 * hide, ungroup or delete a whole group.
 */
export function TradingObjectTreeDrawings({ drawings, eyeIcon, trashIcon, drawingIcon }: {
  drawings: Drawings;
  eyeIcon: (hidden: boolean) => ReactNode;
  trashIcon: ReactNode;
  drawingIcon: (drawing: TradingDrawing) => ReactNode;
}) {
  const [renaming, setRenaming] = useState<Renaming>(null);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(new Set());
  const list = drawings.state.drawings;
  const selected = new Set(selectedDrawingIds(drawings.state));
  const selectedDrawings = list.filter((drawing) => selected.has(drawing.drawingId));

  const drop = (targetId: string) => (event: React.DragEvent) => {
    const id = event.dataTransfer.getData(DRAG_TYPE);
    if (!id) return;
    event.preventDefault();
    drawings.moveTo(id, targetId);
  };
  const dropOnGroup = (group: string) => (event: React.DragEvent) => {
    const id = event.dataTransfer.getData(DRAG_TYPE);
    if (!id) return;
    event.preventDefault();
    drawings.setGroup([id], group);
  };
  const allowDrop = (event: React.DragEvent) => {
    if (event.dataTransfer.types.includes(DRAG_TYPE)) event.preventDefault();
  };

  const row = (drawing: TradingDrawing, grouped: boolean) => {
    const name = drawingTreeName(drawing);
    const isSelected = selected.has(drawing.drawingId);
    const index = list.indexOf(drawing);
    return (
      <li
        key={drawing.drawingId}
        className={`${isSelected ? 'is-selected ' : ''}${drawing.hidden ? 'is-hidden ' : ''}${grouped ? 'is-grouped' : ''}`}
        draggable
        onDragStart={(event) => {
          event.dataTransfer.setData(DRAG_TYPE, drawing.drawingId);
          event.dataTransfer.effectAllowed = 'move';
        }}
        onDragOver={allowDrop}
        onDrop={drop(drawing.drawingId)}
      >
        {renaming?.kind === 'drawing' && renaming.id === drawing.drawingId ? (
          <NameInput value={drawing.name ?? ''} label={`Name of ${name}`} onDone={(text) => { if (text !== null) drawings.rename(drawing.drawingId, text); setRenaming(null); }} />
        ) : (
          <button
            type="button"
            className="trading-object-row-main"
            onClick={(event) => (event.ctrlKey || event.metaKey ? drawings.toggleSelect(drawing.drawingId) : drawings.select(drawing.drawingId))}
            onDoubleClick={() => setRenaming({ kind: 'drawing', id: drawing.drawingId })}
          >
            {drawingIcon(drawing)}
            <span>{name}<small>{drawing.locked ? 'Locked' : drawing.hidden ? 'Hidden' : drawing.name ? drawingDisplayName(drawing) : 'Drawing'}</small></span>
          </button>
        )}
        <div className="trading-object-row-actions">
          <button type="button" aria-label={`Rename ${name}`} title="Rename" onClick={() => setRenaming({ kind: 'drawing', id: drawing.drawingId })}>✎</button>
          <button type="button" aria-label={`Bring ${name} forward`} title="Bring forward" disabled={index === list.length - 1} onClick={() => drawings.reorder(drawing.drawingId, 'up')}>↑</button>
          <button type="button" aria-label={`Send ${name} backward`} title="Send backward" disabled={index === 0} onClick={() => drawings.reorder(drawing.drawingId, 'down')}>↓</button>
          <button type="button" aria-label={`${drawing.hidden ? 'Show' : 'Hide'} ${name}`} title={`${drawing.hidden ? 'Show' : 'Hide'} ${name}`} onClick={() => { drawings.select(drawing.drawingId); drawings.updateSelected({ hidden: !drawing.hidden }); }}>{eyeIcon(Boolean(drawing.hidden))}</button>
          <button type="button" aria-label={`Delete ${name}`} title={`Delete ${name}`} onClick={() => drawings.remove(drawing.drawingId)}>{trashIcon}</button>
        </div>
      </li>
    );
  };

  const groupRow = (group: string, members: TradingDrawing[]) => {
    const open = !collapsed.has(group);
    const hidden = members.every((drawing) => drawing.hidden);
    const ids = members.map((drawing) => drawing.drawingId);
    return (
      <li key={`group:${group}`} className="trading-object-tree-group" onDragOver={allowDrop} onDrop={dropOnGroup(group)}>
        <div className="trading-object-group-row">
          <button
            type="button"
            className="trading-object-row-main"
            aria-expanded={open}
            onClick={() => setCollapsed((current) => {
              const next = new Set(current);
              if (open) next.add(group);
              else next.delete(group);
              return next;
            })}
            onDoubleClick={() => setRenaming({ kind: 'group', id: group })}
          >
            <span className="trading-object-chevron" aria-hidden="true">{open ? '⌄' : '›'}</span>
            {renaming?.kind === 'group' && renaming.id === group ? null : <span>{group}<small>{members.length} drawings</small></span>}
          </button>
          {renaming?.kind === 'group' && renaming.id === group ? (
            <NameInput value={group} label={`Name of group ${group}`} onDone={(text) => { if (text !== null) drawings.renameGroup(group, text); setRenaming(null); }} />
          ) : null}
          <div className="trading-object-row-actions">
            <button type="button" aria-label={`Rename group ${group}`} title="Rename group" onClick={() => setRenaming({ kind: 'group', id: group })}>✎</button>
            <button type="button" aria-label={`${hidden ? 'Show' : 'Hide'} group ${group}`} title={hidden ? 'Show group' : 'Hide group'} onClick={() => drawings.setHidden(ids, !hidden)}>{eyeIcon(hidden)}</button>
            <button type="button" aria-label={`Ungroup ${group}`} title="Ungroup" onClick={() => drawings.setGroup(ids, null)}>⊟</button>
            <button type="button" aria-label={`Delete group ${group}`} title="Delete the group's drawings" onClick={() => drawings.removeMany(ids)}>{trashIcon}</button>
          </div>
        </div>
        {open ? <ul className="trading-object-list">{members.map((drawing) => row(drawing, true))}</ul> : null}
      </li>
    );
  };

  return (
    <>
      <div className="trading-object-tree-tools" role="group" aria-label="Drawing groups">
        <button type="button" disabled={selectedDrawings.length === 0} title="Ctrl+click drawings to select several" onClick={() => drawings.setGroup(selectedDrawings.map((drawing) => drawing.drawingId), nextDrawingGroupName(list))}>
          Group selected ({selectedDrawings.length})
        </button>
        <button type="button" disabled={!selectedDrawings.some((drawing) => drawing.group)} onClick={() => drawings.setGroup(selectedDrawings.map((drawing) => drawing.drawingId), null)}>
          Ungroup
        </button>
      </div>
      <ul className="trading-object-list">
        {treeRows(list).map(([group, members]) => (group ? groupRow(group, members) : row(members[0], false)))}
      </ul>
    </>
  );
}
