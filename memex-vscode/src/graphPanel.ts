import * as vscode from 'vscode';
import * as path from 'path';
import * as fs from 'fs';
import { MemexClient } from './memexClient';

export class MemexGraphPanel implements vscode.WebviewViewProvider {
  public static readonly viewType = 'memex-graph';
  private _view?: vscode.WebviewView;

  constructor(
    private readonly _extensionUri: vscode.Uri,
    private readonly _client: MemexClient,
    private readonly _onStaleRatioChange: (staleRatio: number) => void
  ) {}

  public resolveWebviewView(
    webviewView: vscode.WebviewView,
    context: vscode.WebviewViewResolveContext,
    _token: vscode.CancellationToken
  ) {
    this._view = webviewView;

    webviewView.webview.options = {
      enableScripts: true,
      localResourceRoots: [
        this._extensionUri
      ]
    };

    webviewView.webview.html = this._getHtmlForWebview(webviewView.webview);

    webviewView.webview.onDidReceiveMessage(async (data) => {
      switch (data.type) {
        case 'ready': {
          await this.refresh();
          break;
        }
        case 'openFile': {
          const filePath = data.path;
          if (filePath) {
            const workspaceFolders = vscode.workspace.workspaceFolders;
            if (workspaceFolders) {
              const fullPath = vscode.Uri.file(path.join(workspaceFolders[0].uri.fsPath, filePath));
              try {
                const doc = await vscode.workspace.openTextDocument(fullPath);
                await vscode.window.showTextDocument(doc);
              } catch (e) {
                vscode.window.showErrorMessage(`Failed to open file: ${filePath}`);
              }
            }
          }
          break;
        }
      }
    });

    vscode.window.onDidChangeActiveTextEditor((editor) => {
      if (editor) {
        this.highlightActiveEditor(editor);
      }
    });
    if (vscode.window.activeTextEditor) {
      this.highlightActiveEditor(vscode.window.activeTextEditor);
    }
  }

  public async refresh(): Promise<number> {
    if (!this._view) {
      return 0;
    }

    try {
      const data = await this._client.fetchGraph();
      this._view.webview.postMessage({
        type: 'updateGraph',
        data: data
      });
      
      // Calculate stale ratio based on mock/simple criteria
      // e.g. nodes with low confidence or empty summaries (placeholder check)
      const staleCount = data.nodes.filter(n => n.summary === 'stale').length;
      const staleRatio = data.nodes.length > 0 ? staleCount / data.nodes.length : 0;
      this._onStaleRatioChange(staleRatio);
      
      if (vscode.window.activeTextEditor) {
        this.highlightActiveEditor(vscode.window.activeTextEditor);
      }
      return staleRatio;
    } catch (err) {
      this._view.webview.postMessage({
        type: 'error',
        message: (err as Error).message
      });
      return 0;
    }
  }

  public highlightActiveEditor(editor: vscode.TextEditor) {
    if (!this._view) {
      return;
    }

    const document = editor.document;
    const workspaceFolders = vscode.workspace.workspaceFolders;
    if (workspaceFolders) {
      const relPath = path.relative(workspaceFolders[0].uri.fsPath, document.uri.fsPath).replace(/\\/g, '/');
      this._view.webview.postMessage({
        type: 'highlightActiveFile',
        path: relPath
      });
    }
  }

  private _getHtmlForWebview(webview: vscode.Webview): string {
    const htmlPath = vscode.Uri.joinPath(this._extensionUri, 'webview', 'graph.html');
    const cssUri = webview.asWebviewUri(vscode.Uri.joinPath(this._extensionUri, 'webview', 'graph.css'));
    const jsUri = webview.asWebviewUri(vscode.Uri.joinPath(this._extensionUri, 'webview', 'graph.js'));

    let html = fs.readFileSync(htmlPath.fsPath, 'utf8');
    
    html = html.replace('href="graph.css"', `href="${cssUri}"`);
    html = html.replace('src="graph.js"', `src="${jsUri}"`);

    return html;
  }
}
