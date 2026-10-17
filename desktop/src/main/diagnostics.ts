import { app, dialog, type BrowserWindow } from 'electron';
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import type { DesktopPaths } from './paths';
import type { DesktopSettings } from './settings';

const MAX_LOG_BYTES = 2 * 1024 * 1024;

const CRC_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let index = 0; index < 256; index += 1) {
    let value = index;
    for (let bit = 0; bit < 8; bit += 1) value = value & 1 ? 0xedb88320 ^ (value >>> 1) : value >>> 1;
    table[index] = value >>> 0;
  }
  return table;
})();

function crc32(data: Buffer): number {
  let crc = 0xffffffff;
  for (const byte of data) crc = CRC_TABLE[(crc ^ byte) & 0xff]! ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** A zip archive of stored (uncompressed) files. */
export function zip(files: Array<{ name: string; data: Buffer }>): Buffer {
  const local: Buffer[] = [];
  const central: Buffer[] = [];
  let offset = 0;
  for (const file of files) {
    const name = Buffer.from(file.name, 'utf8');
    const crc = crc32(file.data);
    const header = Buffer.alloc(30);
    header.writeUInt32LE(0x04034b50, 0);
    header.writeUInt16LE(20, 4);
    header.writeUInt16LE(0x0800, 6); // UTF-8 names
    header.writeUInt32LE(crc, 14);
    header.writeUInt32LE(file.data.length, 18);
    header.writeUInt32LE(file.data.length, 22);
    header.writeUInt16LE(name.length, 26);
    local.push(header, name, file.data);
    const entry = Buffer.alloc(46);
    entry.writeUInt32LE(0x02014b50, 0);
    entry.writeUInt16LE(20, 4);
    entry.writeUInt16LE(20, 6);
    entry.writeUInt16LE(0x0800, 8);
    entry.writeUInt32LE(crc, 16);
    entry.writeUInt32LE(file.data.length, 20);
    entry.writeUInt32LE(file.data.length, 24);
    entry.writeUInt16LE(name.length, 28);
    entry.writeUInt32LE(offset, 42);
    central.push(entry, name);
    offset += header.length + name.length + file.data.length;
  }
  const centralSize = central.reduce((size, part) => size + part.length, 0);
  const end = Buffer.alloc(22);
  end.writeUInt32LE(0x06054b50, 0);
  end.writeUInt16LE(files.length, 8);
  end.writeUInt16LE(files.length, 10);
  end.writeUInt32LE(centralSize, 12);
  end.writeUInt32LE(offset, 16);
  return Buffer.concat([...local, ...central, end]);
}

function tail(path: string): Buffer {
  const data = readFileSync(path);
  return data.length > MAX_LOG_BYTES ? data.subarray(data.length - MAX_LOG_BYTES) : data;
}

/** The files a diagnostics archive holds: logs (their last 2 MB), settings without secrets, versions. */
export function diagnosticsFiles(paths: DesktopPaths, settings: DesktopSettings | null): Array<{ name: string; data: Buffer }> {
  const files: Array<{ name: string; data: Buffer }> = [];
  if (existsSync(paths.logs)) {
    for (const name of readdirSync(paths.logs)) {
      const path = join(paths.logs, name);
      if (statSync(path).isFile()) files.push({ name: `logs/${name}`, data: tail(path) });
    }
  }
  const buildInfo = join(paths.resources, 'build-info.json');
  files.push({
    name: 'versions.json',
    data: Buffer.from(JSON.stringify({
      app: app.getVersion(),
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      os: `${process.platform} ${process.getSystemVersion()}`,
      build: existsSync(buildInfo) ? JSON.parse(readFileSync(buildInfo, 'utf8')) : null,
    }, null, 2)),
  });
  // desktop.json holds ports, preferences and window bounds: no secrets (those are in desktop-secrets.bin).
  if (settings) files.push({ name: 'desktop.json', data: Buffer.from(JSON.stringify(settings, null, 2)) });
  return files;
}

export async function exportDiagnostics(paths: DesktopPaths, settings: DesktopSettings | null, parent: BrowserWindow | null): Promise<void> {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  const options = {
    title: 'Export MarketPulse diagnostics',
    defaultPath: join(app.getPath('downloads'), `marketpulse-diagnostics-${stamp}.zip`),
    filters: [{ name: 'Zip archive', extensions: ['zip'] }],
  };
  const { canceled, filePath } = parent ? await dialog.showSaveDialog(parent, options) : await dialog.showSaveDialog(options);
  if (canceled || !filePath) return;
  writeFileSync(filePath, zip(diagnosticsFiles(paths, settings)));
}
