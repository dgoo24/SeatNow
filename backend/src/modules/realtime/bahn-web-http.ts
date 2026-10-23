/**
 * Cliente HTTP para los endpoints públicos de bahn.de.
 *
 * bahn.de protege la API con un anti-bot OPS que bloquea la pila TLS de Node
 * (OpenSSL) pero admite Schannel (.NET/PowerShell). En Windows este puente
 * ejecuta powershell.exe + Invoke-RestMethod y parsea su JSON; fuera de Windows
 * (o si el puente falla) se intenta fetch simple.
 */

import { execFile } from 'node:child_process';

export const BAHN_USER_AGENT =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36';

function psInvokeRestMethod(url: string): Promise<string> {
  const script =
    "$ErrorActionPreference='Stop';" +
    '[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;' +
    `(Invoke-RestMethod -Uri '${url}' -Method Get -TimeoutSec 15 ` +
    `-Headers @{Accept='application/json';'User-Agent'='${BAHN_USER_AGENT}';Referer='https://www.bahn.de/'}) ` +
    '| ConvertTo-Json -Depth 12 -Compress';
  return new Promise((resolve, reject) => {
    execFile(
      'powershell.exe',
      ['-NoProfile', '-NonInteractive', '-Command', script],
      { timeout: 25_000, maxBuffer: 8 * 1024 * 1024, windowsHide: true, encoding: 'utf8' },
      (err, stdout) => {
        if (err) return reject(err);
        if (!stdout.trim()) return reject(new Error('Leere Antwort von PowerShell'));
        resolve(stdout);
      },
    );
  });
}

async function plainFetch(url: string): Promise<unknown> {
  const res = await fetch(url, {
    headers: {
      Accept: 'application/json',
      'User-Agent': BAHN_USER_AGENT,
      Referer: 'https://www.bahn.de/',
    },
    signal: AbortSignal.timeout(8000),
  });
  if (!res.ok) throw new Error(`bahn.de nicht verfügbar (HTTP ${res.status})`);
  return res.json();
}

export async function bahnGetJson<T>(url: string): Promise<T> {
  if (process.platform === 'win32') {
    try {
      const raw = await psInvokeRestMethod(url);
      return JSON.parse(raw) as T;
    } catch {
      /* intenta fetch directo */
    }
  }
  return (await plainFetch(url)) as T;
}
