// The "Import market data from Omnix" page (roadmap MP-10.1): a dry run first, then the import.
interface ImportTable {
  table: string;
  source_rows: number;
  new_rows: number;
}

interface ImportResult {
  applied?: boolean;
  tables?: ImportTable[];
  error?: string;
}

interface ImportBridge {
  importReference(source: string, apply: boolean): Promise<ImportResult>;
  closeWindow(): void;
}

const bridge = (window as unknown as { marketPulseShell?: ImportBridge }).marketPulseShell;
const source = document.getElementById('source') as HTMLInputElement;
const state = document.getElementById('state') as HTMLParagraphElement;
const report = document.getElementById('report') as HTMLTableElement;
const newHeading = document.getElementById('new-heading') as HTMLTableCellElement;
const check = document.getElementById('check') as HTMLButtonElement;
const apply = document.getElementById('apply') as HTMLButtonElement;
let checkedSource: string | null = null;

function label(table: string): string {
  const name = table.replace(/^omnix_trading_/, '').replaceAll('_', ' ');
  return name.charAt(0).toUpperCase() + name.slice(1);
}

function show(result: ImportResult): void {
  const body = report.tBodies[0];
  body.replaceChildren();
  report.hidden = !result.tables;
  for (const table of result.tables ?? []) {
    const row = body.insertRow();
    row.insertCell().textContent = label(table.table);
    row.insertCell().textContent = table.source_rows.toLocaleString();
    row.insertCell().textContent = table.new_rows.toLocaleString();
  }
}

async function run(write: boolean): Promise<void> {
  const url = source.value.trim();
  if (!bridge || !url) return;
  check.disabled = apply.disabled = source.disabled = true;
  state.textContent = write ? 'Importing…' : 'Checking…';
  try {
    const result = await bridge.importReference(url, write);
    if (result.error) {
      state.textContent = result.error;
      show({});
      checkedSource = null;
      return;
    }
    show(result);
    const added = (result.tables ?? []).reduce((sum, table) => sum + table.new_rows, 0);
    newHeading.textContent = write ? 'Added' : 'New';
    if (write) {
      state.textContent = added ? `Imported ${added.toLocaleString()} rows.` : 'Nothing to import: MarketPulse has all of it.';
      checkedSource = null;
    } else {
      state.textContent = added ? `${added.toLocaleString()} rows to import.` : 'Nothing to import: MarketPulse has all of it.';
      checkedSource = added ? url : null;
    }
  } finally {
    check.disabled = source.disabled = false;
    apply.disabled = checkedSource === null || checkedSource !== source.value.trim();
  }
}

check.addEventListener('click', () => void run(false));
apply.addEventListener('click', () => void run(true));
source.addEventListener('input', () => {
  // Import only what the last check covered.
  apply.disabled = checkedSource === null || checkedSource !== source.value.trim();
});
source.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') void run(false);
});
document.getElementById('close')?.addEventListener('click', () => bridge?.closeWindow());

export {};
