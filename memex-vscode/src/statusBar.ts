import * as vscode from 'vscode';

export class MemexStatusBarItem {
  private item: vscode.StatusBarItem;

  constructor() {
    this.item = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
    this.item.command = 'memex.openGraph';
    this.item.text = '$(x) memex offline';
    this.item.tooltip = 'memex local server is offline';
    this.item.show();
  }

  public setOffline() {
    this.item.text = '$(x) memex offline';
    this.item.tooltip = 'memex local server is offline';
    this.item.color = undefined;
  }

  public setConnected(staleRatio: number = 0, savedTokens?: number) {
    let suffix = '';
    if (savedTokens !== undefined && savedTokens > 0) {
      const formatted = savedTokens.toLocaleString();
      suffix = `  |  saved ~${formatted} tokens today`;
    }

    if (staleRatio > 0.1) {
      this.item.text = `$(warning) memex${suffix}`;
      this.item.tooltip = `memex connected — stale nodes: ${(staleRatio * 100).toFixed(0)}%`;
      this.item.color = new vscode.ThemeColor('statusBarItem.warningForeground');
    } else {
      this.item.text = `$(database) memex${suffix}`;
      this.item.tooltip = 'memex connected — graph is up to date';
      this.item.color = undefined;
    }
  }

  public dispose() {
    this.item.dispose();
  }
}
