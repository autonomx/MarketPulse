// The "Import from Omnix" page (roadmap MP-10.1 and MP-10.2): a dry run first, then the import.
interface ImportTable {
  table: string;
  source_rows: number;
  new_rows: number;
}

interface ImportGroup {
  group: string;
  in_omnix: number;
  new: number;
}

interface ImportResult {
  applied?: boolean;
  tables?: ImportTable[];
  groups?: ImportGroup[];
  error?: string;
}

interface ImportBridge {
  importReference(source: string, apply: boolean): Promise<ImportResult>;
  importAccountData(source: string, apply: boolean, workspace: string): Promise<ImportResult>;
  closeWindow(): void;
}

interface Line {
  label: string;
  inOmnix: number;
  added: number;
}

const GROUP_LABELS: Record<string, string> = {
  documents: 'Layouts, watchlists, drawings and scripts',
  alerts: 'Alerts',
  paper_accounts: 'Paper accounts',
};

const bridge = (window as unknown as { marketPulseShell?: ImportBridge }).marketPulseShell;
const source = document.getElementById('source') as HTMLInputElement;
const accounts = document.getElementById('accounts') as HTMLInputElement;
const workspace = document.getElementById('workspace') as HTMLInputElement;
const workspaceRow = document.getElementById('workspace-row') as HTMLDivElement;
const state = document.getElementById('state') as HTMLParagraphElement;
const report = document.getElementById('report') as HTMLTableElement;
const newHeading = document.getElementById('new-heading') as HTMLTableCellElement;
const check = document.getElementById('check') as HTMLButtonElement;
const apply = document.getElementById('apply') as HTMLButtonElement;
let checked: string | null = null;

/** What the last check covered: the source, and the workspace when account data is included. */
function request(): string {
  return JSON.stringify([source.value.trim(), accounts.checked ? workspace.value.trim() : null]);
}

function tableLabel(table: string): string {
  const name = table.replace(/^omnix_trading_/, '').replaceAll('_', ' ');
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function show(lines: Line[] | null): void {
  const body = report.tBodies[0];
  body.replaceChildren();
  report.hidden = lines === null;
  for (const line of lines ?? []) {
    const row = body.insertRow();
    row.insertCell().textContent = line.label;
    row.insertCell().textContent = line.inOmnix.toLocaleString();
    row.insertCell().textContent = line.added.toLocaleString();
  }
}

async function run(write: boolean): Promise<void> {
  const url = source.value.trim();
  if (!bridge || !url) return;
  const covered = request();
  check.disabled = apply.disabled = source.disabled = accounts.disabled = workspace.disabled = true;
  state.textContent = write ? 'Importing…' : 'Checking…';
  try {
    const reference = await bridge.importReference(url, write);
    const own = !reference.error && accounts.checked
      ? await bridge.importAccountData(url, write, workspace.value.trim())
      : null;
    const error = reference.error ?? own?.error;
    if (error) {
      state.textContent = error;
      show(null);
      checked = null;
      return;
    }
    const lines: Line[] = [
      ...(reference.tables ?? []).map((table) => ({ label: tableLabel(table.table), inOmnix: table.source_rows, added: table.new_rows })),
      ...(own?.groups ?? []).map((group) => ({ label: GROUP_LABELS[group.group] ?? group.group, inOmnix: group.in_omnix, added: group.new })),
    ];
    show(lines);
    const added = lines.reduce((sum, line) => sum + line.added, 0);
    newHeading.textContent = write ? 'Added' : 'New';
    if (!added) state.textContent = 'Nothing to import: MarketPulse has all of it.';
    else state.textContent = write ? `Imported ${added.toLocaleString()} items.` : `${added.toLocaleString()} items to import.`;
    checked = !write && added ? covered : null;
  } finally {
    check.disabled = source.disabled = accounts.disabled = workspace.disabled = false;
    apply.disabled = checked === null || checked !== request();
  }
}

function changed(): void {
  // Import only what the last check covered.
  workspaceRow.hidden = !accounts.checked;
  apply.disabled = checked === null || checked !== request();
}

check.addEventListener('click', () => void run(false));
apply.addEventListener('click', () => void run(true));
for (const input of [source, workspace]) input.addEventListener('input', changed);
accounts.addEventListener('change', changed);
source.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') void run(false);
});
document.getElementById('close')?.addEventListener('click', () => bridge?.closeWindow());

export {};
