import { Badge, Box, Group, Paper, Progress, Stack, Text, Title } from '@mantine/core';
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { getOmnixThemePreset, OMNIX_THEME_PRESETS, type OmnixThemeId } from './appearanceThemes';

export function OmnixShellLayout({ children, isSidebarVisible = true, sidebar, topbar }: { children: ReactNode; isSidebarVisible?: boolean; sidebar: ReactNode; topbar: ReactNode }) {
  const shellClassName = isSidebarVisible ? 'omnix-shell' : 'omnix-shell sidebar-hidden';
  return <Box className={shellClassName}>{topbar}{sidebar}<main className="omnix-main">{children}</main></Box>;
}

export function OmnixSidebar({ children, hidden = false }: { children: ReactNode; hidden?: boolean }) {
  const [expanded, setExpanded] = useState(true);
  useEffect(() => { if (!hidden) setExpanded(true); }, [hidden]);
  const sidebarClassName = [expanded ? 'omnix-sidebar expanded' : 'omnix-sidebar collapsed', hidden ? 'hidden' : ''].filter(Boolean).join(' ');
  return (
    <aside id="omnix-sidebar" className={sidebarClassName} aria-hidden={hidden} aria-label="Omnix navigation">
      <button className="omnix-sidebar-toggle" type="button" aria-label={expanded ? 'Collapse sidebar' : 'Expand sidebar'} aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}><span aria-hidden="true">☰</span></button>
      {children}
    </aside>
  );
}

export function OmnixBrand() {
  return <div role="group" className="omnix-brand" aria-label="Omnix"><span className="omnix-brand-mark" aria-hidden="true" /><span className="omnix-brand-copy"><h1>Omnix</h1><small>Local AI workstation</small></span></div>;
}

export function OmnixNavItem({ active, icon, children }: { active: boolean; icon: string; children: ReactNode }) {
  return <span className={active ? 'omnix-nav-item active' : 'omnix-nav-item'}><span className="omnix-nav-icon" aria-hidden="true">{icon}</span><span className="omnix-nav-label">{children}</span></span>;
}

function OmnixThemePicker({ themeId, onThemeChange }: { themeId: OmnixThemeId; onThemeChange?: (themeId: OmnixThemeId) => void }) {
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement | null>(null);
  const currentTheme = getOmnixThemePreset(themeId);
  useEffect(() => {
    if (!open) return undefined;
    const closeOnOutsidePointer = (event: PointerEvent) => { if (!rootRef.current?.contains(event.target as Node)) setOpen(false); };
    const closeOnEscape = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false); };
    document.addEventListener('pointerdown', closeOnOutsidePointer); document.addEventListener('keydown', closeOnEscape);
    return () => { document.removeEventListener('pointerdown', closeOnOutsidePointer); document.removeEventListener('keydown', closeOnEscape); };
  }, [open]);
  return (
    <div className="omnix-theme-picker" ref={rootRef}>
      <button className="omnix-theme-picker-trigger" type="button" aria-haspopup="listbox" aria-expanded={open} aria-label={`Choose theme. Current theme: ${currentTheme.label}`} title={`Theme: ${currentTheme.label}`} onClick={() => setOpen((value) => !value)}>
        <span className="omnix-theme-swatch" style={{ background: currentTheme.preview }} aria-hidden="true" /><span className="omnix-theme-picker-label">{currentTheme.label}</span><span className="omnix-theme-picker-chevron" aria-hidden="true">⌄</span>
      </button>
      {open ? <div className="omnix-theme-menu" role="listbox" aria-label="Omnix themes"><div className="omnix-theme-menu-heading"><strong>Choose a theme</strong><small>Palette changes apply instantly.</small></div>{OMNIX_THEME_PRESETS.map((theme) => <button key={theme.id} className={theme.id === themeId ? 'omnix-theme-option active' : 'omnix-theme-option'} type="button" role="option" aria-selected={theme.id === themeId} onClick={() => { onThemeChange?.(theme.id); setOpen(false); }}><span className="omnix-theme-option-swatch" style={{ background: theme.preview }} aria-hidden="true" /><span><strong>{theme.label}</strong><small>{theme.description}</small></span><b aria-hidden="true">✓</b></button>)}</div> : null}
    </div>
  );
}

export function OmnixTopBar({ isSidebarVisible = true, onToggleSidebar, onToggleTheme, onThemeChange, themeId = 'aurora', themeMode = 'dark', title, status = 'Local-first', children }: { isSidebarVisible?: boolean; onToggleSidebar?: () => void; onToggleTheme?: () => void; onThemeChange?: (themeId: OmnixThemeId) => void; themeId?: OmnixThemeId; themeMode?: 'light' | 'dark'; title: string; status?: string; children?: ReactNode }) {
  return (
    <header className="omnix-topbar">
      <button className="omnix-shell-toggle" type="button" aria-controls="omnix-sidebar" aria-expanded={isSidebarVisible} aria-label={isSidebarVisible ? 'Hide Omnix sidebar' : 'Show Omnix sidebar'} title={isSidebarVisible ? 'Hide Omnix sidebar' : 'Show Omnix sidebar'} onClick={onToggleSidebar}><span aria-hidden="true" data-direction={isSidebarVisible ? 'left' : 'right'}>{isSidebarVisible ? '‹' : '›'}</span></button>
      <div role="group" className="omnix-topbar-brand" aria-label="Omnix"><strong>Omnix</strong><small>Local AI workstation</small></div>
      {children ? <div role="group" className="omnix-mode-tabs" aria-label="Workspace modes">{children}</div> : null}
      <div className="omnix-topbar-actions"><OmnixThemePicker themeId={themeId} onThemeChange={onThemeChange} /><button className="omnix-theme-toggle" type="button" aria-label={themeMode === 'light' ? 'Switch to dark mode' : 'Switch to light mode'} aria-pressed={themeMode === 'light'} title={themeMode === 'light' ? 'Switch to dark mode' : 'Switch to light mode'} onClick={onToggleTheme}><span className="omnix-theme-toggle-glyph" aria-hidden="true" /></button><div role="group" className="omnix-topbar-status" aria-label={`${title}, ${status}`}><span>Assistant</span><b>{title}</b><b>{status}</b></div></div>
    </header>
  );
}

export function OmnixStatusPill({ children }: { children: ReactNode }) { return <Badge className="status-pill" component="span" variant="light">{children}</Badge>; }
/** A workspace's outer panel; name it by its heading's id (`labelledBy`) or, without a visible heading, by `label`. */
export function WorkspacePanel({ children, className, labelledBy, label }: { children: ReactNode; className?: string; labelledBy?: string; label?: string }) { const panelClassName = className ? `workspace-card ${className}` : 'workspace-card'; return <Paper className={panelClassName} component="section" aria-labelledby={labelledBy} aria-label={labelledBy ? undefined : label}>{children}</Paper>; }
export function OmnixProgressLog({ value, logs }: { value: number; logs: Array<{ level: string; message: string }> }) { return <Stack gap="xs"><Progress value={value} aria-label="Progress" /><Stack className="omnix-log-viewer" gap={4}>{logs.map((log, index) => <Text key={`${log.level}-${index}`} size="sm"><strong>{log.level}</strong> {log.message}</Text>)}</Stack></Stack>; }
export function OmnixTranscriptView({ messages }: { messages: Array<{ role: string; content: string }> }) { return <Stack className="omnix-transcript" gap="xs">{messages.map((message, index) => <Paper key={`${message.role}-${index}`} className="omnix-message" component="article"><Text size="xs" tt="uppercase">{message.role}</Text><Text>{message.content}</Text></Paper>)}</Stack>; }
export function OmnixAudioControls({ label }: { label: string }) { return <Group className="omnix-audio-controls" gap="sm"><button type="button" aria-label={`Play ${label}`}>Play</button><button type="button" aria-label={`Stop ${label}`}>Stop</button><Text size="sm">{label}</Text></Group>; }
export function OmnixAssetCard({ title, metadata }: { title: string; metadata: string }) { return <Paper className="omnix-asset-card" component="article"><Title order={4}>{title}</Title><Text size="sm">{metadata}</Text></Paper>; }
export function OmnixDiagnosticsView({ rows }: { rows: Array<{ label: string; value: string }> }) { return <Stack className="omnix-diagnostics" gap="xs">{rows.map((row) => <Group key={row.label} justify="space-between"><Text size="sm">{row.label}</Text><OmnixStatusPill>{row.value}</OmnixStatusPill></Group>)}</Stack>; }
