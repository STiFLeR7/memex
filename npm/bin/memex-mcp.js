#!/usr/bin/env node

const { execSync, spawn } = require('child_process');

function isCommand(cmd) {
  try {
    execSync(`${cmd} --version`, { stdio: 'ignore' });
    return true;
  } catch {
    return false;
  }
}

// Prerequisite check: uv is required for the most reliable experience
if (!isCommand('uv')) {
  console.error('[stifler-memex-mcp] uv is required but not found.');
  console.error('[stifler-memex-mcp] Install it from: https://docs.astral.sh/uv/getting-started/installation/');
  process.exit(1);
}

// Delegate all args to the Python memex CLI via 'uv tool run'
// This ensures that the package is installed in an isolated environment with all dependencies.
const args = process.argv.slice(2);

// We use '--from memex-mcp' because the package name is memex-mcp on PyPI,
// but it provides the 'memex' executable.
const child = spawn('uv', ['tool', 'run', '--from', 'memex-mcp', 'memex', ...args], {
  stdio: 'inherit',
  shell: process.platform === 'win32',
});

child.on('exit', (code) => process.exit(code ?? 0));
