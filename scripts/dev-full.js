import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const isWindows = process.platform === 'win32';
const python = path.join(projectRoot, '.venv', isWindows ? 'Scripts/python.exe' : 'bin/python');
const vite = path.join(projectRoot, 'node_modules', 'vite', 'bin', 'vite.js');

if (!existsSync(python)) {
  console.error('Python environment not found. Create the project .venv and install backend/requirements.txt first.');
  process.exit(1);
}
if (!existsSync(vite)) {
  console.error('Vite is not installed. Run npm install first.');
  process.exit(1);
}

console.log('Starting Mausam frontend (http://127.0.0.1:5173) and API (http://127.0.0.1:8000)…');

const processes = [
  spawn(python, ['-m', 'uvicorn', 'backend.app.main:app', '--host', '127.0.0.1', '--port', '8000'], {
    cwd: projectRoot,
    stdio: 'inherit',
  }),
  spawn(process.execPath, [vite, '--host', '127.0.0.1'], {
    cwd: projectRoot,
    stdio: 'inherit',
  }),
];

let stopping = false;
function stopAll(exitCode) {
  if (stopping) return;
  stopping = true;
  process.exitCode = exitCode;
  for (const child of processes) {
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
  }
  const forceStop = setTimeout(() => {
    for (const child of processes) {
      if (child.exitCode === null && child.signalCode === null) child.kill('SIGKILL');
    }
  }, 3000);
  forceStop.unref();
}

for (const child of processes) {
  child.on('error', error => {
    console.error(`Could not start ${child === processes[0] ? 'API' : 'Vite'}: ${error.message}`);
    stopAll(1);
  });
  child.on('exit', code => {
    if (!stopping) {
      console.error(`A Mausam development service exited (code ${code ?? 'unknown'}). Stopping the other service.`);
      stopAll(code ?? 1);
    }
  });
}

process.on('SIGINT', () => stopAll(0));
process.on('SIGTERM', () => stopAll(0));
