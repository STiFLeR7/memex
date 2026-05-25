import * as vscode from 'vscode';
import * as child_process from 'child_process';
import { MemexClient } from './memexClient';
import { MemexGraphPanel } from './graphPanel';
import { MemexStatusBarItem } from './statusBar';

let serverProcess: child_process.ChildProcess | null = null;
let client: MemexClient | null = null;
let panelProvider: MemexGraphPanel | null = null;
let statusBar: MemexStatusBarItem | null = null;
let healthCheckInterval: NodeJS.Timeout | null = null;
let isConnected = false;

export function activate(context: vscode.ExtensionContext) {
  const workspaceFolders = vscode.workspace.workspaceFolders;
  const workspaceRoot = workspaceFolders ? workspaceFolders[0].uri.fsPath : '.';

  const config = vscode.workspace.getConfiguration('memex');
  const initialPort = config.get<number>('serverPort') || 7463;

  client = new MemexClient('127.0.0.1', initialPort, workspaceRoot);
  statusBar = new MemexStatusBarItem();

  panelProvider = new MemexGraphPanel(
    context.extensionUri,
    client,
    (staleRatio) => {
      if (statusBar && isConnected) {
        statusBar.setConnected(staleRatio);
      }
    }
  );

  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(
      MemexGraphPanel.viewType,
      panelProvider
    )
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('memex.openGraph', () => {
      vscode.commands.executeCommand('workbench.view.extension.memex-container');
      vscode.commands.executeCommand('memex-graph.focus');
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('memex.refreshGraph', async () => {
      if (panelProvider) {
        await panelProvider.refresh();
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('memex.showFileContext', () => {
      const editor = vscode.window.activeTextEditor;
      if (editor && panelProvider) {
        panelProvider.highlightActiveEditor(editor);
      }
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('memex.startServer', () => {
      if (serverProcess) {
        vscode.window.showInformationMessage('memex server is already running.');
        return;
      }

      const config = vscode.workspace.getConfiguration('memex');
      const port = config.get<number>('serverPort') || 7463;

      vscode.window.withProgress({
        location: vscode.ProgressLocation.Notification,
        title: "Starting memex server...",
        cancellable: false
      }, async () => {
        return new Promise<void>((resolve) => {
          const env = { ...process.env };
          
          serverProcess = child_process.spawn(
            'uv',
            ['run', 'memex', 'serve', '--repo', workspaceRoot, '--transport', 'http', '--port', port.toString()],
            {
              cwd: workspaceRoot,
              env: env,
              shell: true
            }
          );

          serverProcess.stdout?.on('data', (data) => {
            console.log(`[memex stdout] ${data}`);
          });

          serverProcess.stderr?.on('data', (data) => {
            console.error(`[memex stderr] ${data}`);
          });

          serverProcess.on('close', (code) => {
            console.log(`memex server exited with code ${code}`);
            serverProcess = null;
            isConnected = false;
            if (statusBar) {
              statusBar.setOffline();
            }
            if (code === 1) {
              vscode.window.showErrorMessage(`memex server failed to bind to port ${port}. The port might be in use. Please check or change 'memex.serverPort' in settings.`);
            }
          });

          serverProcess.on('error', (err) => {
            console.error(`Failed to start memex server: ${err}`);
            vscode.window.showErrorMessage(`Failed to start memex server. Ensure 'uv' is installed and in your PATH. Error: ${err.message}`);
            serverProcess = null;
            isConnected = false;
            if (statusBar) {
              statusBar.setOffline();
            }
          });

          setTimeout(() => {
            resolve();
          }, 2000);
        });
      });
    })
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('memex.stopServer', () => {
      if (!serverProcess) {
        vscode.window.showInformationMessage('memex server is not running.');
        return;
      }
      
      serverProcess.kill();
      serverProcess = null;
      isConnected = false;
      if (statusBar) {
        statusBar.setOffline();
      }
      vscode.window.showInformationMessage('memex server stopped.');
    })
  );

  const checkHealth = async () => {
    if (!client || !statusBar || !panelProvider) {
      return;
    }
    const currentConfig = vscode.workspace.getConfiguration('memex');
    const port = currentConfig.get<number>('serverPort') || 7463;
    client.setPort(port);

    const currentlyHealthy = await client.checkHealth();
    if (currentlyHealthy && !isConnected) {
      isConnected = true;
      statusBar.setConnected(0);
      
      client.subscribeToEvents(
        () => {
          if (panelProvider) {
            panelProvider.refresh();
          }
        },
        (err) => {
          console.error('SSE Error:', err);
          isConnected = false;
          statusBar?.setOffline();
        }
      );

      panelProvider.refresh();
    } else if (!currentlyHealthy && isConnected) {
      isConnected = false;
      statusBar.setOffline();
      client.unsubscribe();
    }
  };

  checkHealth();
  healthCheckInterval = setInterval(checkHealth, 5000);
}

export function deactivate() {
  if (healthCheckInterval) {
    clearInterval(healthCheckInterval);
  }
  if (serverProcess) {
    serverProcess.kill();
  }
  if (client) {
    client.unsubscribe();
  }
  if (statusBar) {
    statusBar.dispose();
  }
}
