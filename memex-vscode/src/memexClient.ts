import * as http from 'http';
import { GraphData } from './types';

export class MemexClient {
  private host: string;
  private port: number;
  private sseRequest: http.ClientRequest | null = null;
  private activeRepo: string;

  constructor(host: string = '127.0.0.1', port: number = 7463, repo: string = '.') {
    this.host = host;
    this.port = port;
    this.activeRepo = repo;
  }

  public getBaseUrl(): string {
    return `http://${this.host}:${this.port}`;
  }

  public setPort(port: number) {
    this.port = port;
  }

  public getPort(): number {
    return this.port;
  }

  public getHost(): string {
    return this.host;
  }

  public async checkHealth(): Promise<boolean> {
    return new Promise((resolve) => {
      const req = http.get(`${this.getBaseUrl()}/health`, { timeout: 1000 }, (res) => {
        if (res.statusCode === 200) {
          resolve(true);
        } else {
          resolve(false);
        }
      });
      req.on('error', () => resolve(false));
      req.on('timeout', () => {
        req.destroy();
        resolve(false);
      });
    });
  }

  public async fetchGraph(): Promise<GraphData> {
    return new Promise((resolve, reject) => {
      const req = http.get(`${this.getBaseUrl()}/graph`, { timeout: 3000 }, (res) => {
        let data = '';
        res.on('data', (chunk) => {
          data += chunk;
        });
        res.on('end', () => {
          if (res.statusCode === 200) {
            try {
              resolve(JSON.parse(data));
            } catch (err) {
              reject(new Error(`Failed to parse graph JSON: ${(err as Error).message}`));
            }
          } else {
            reject(new Error(`Server returned status code ${res.statusCode}: ${data}`));
          }
        });
      });
      req.on('error', (err) => reject(err));
    });
  }

  public subscribeToEvents(onUpdate: () => void, onError: (err: Error) => void) {
    this.unsubscribe();

    const options = {
      headers: {
        'Accept': 'text/event-stream'
      },
      timeout: 0
    };

    const url = `${this.getBaseUrl()}/events`;
    this.sseRequest = http.get(url, options, (res) => {
      if (res.statusCode !== 200) {
        onError(new Error(`SSE connection failed with status: ${res.statusCode}`));
        return;
      }

      let buffer = '';
      res.on('data', (chunk) => {
        buffer += chunk.toString();
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('event: graph_updated')) {
            onUpdate();
          }
        }
      });

      res.on('end', () => {
        onError(new Error('SSE connection closed by server'));
      });
    });

    this.sseRequest.on('error', (err) => {
      onError(err);
    });
  }

  public async fetchStats(token: string): Promise<any> {
    return new Promise((resolve, reject) => {
      const url = `${this.getBaseUrl()}/stats?days=1`;
      const options = {
        headers: {
          'Authorization': `Bearer ${token}`
        },
        timeout: 2000
      };
      const req = http.get(url, options, (res) => {
        let data = '';
        res.on('data', (chunk) => {
          data += chunk;
        });
        res.on('end', () => {
          if (res.statusCode === 200) {
            try {
              resolve(JSON.parse(data));
            } catch (err) {
              reject(new Error(`Failed to parse stats JSON: ${(err as Error).message}`));
            }
          } else {
            reject(new Error(`Server returned status code ${res.statusCode}: ${data}`));
          }
        });
      });
      req.on('error', (err) => reject(err));
      req.on('timeout', () => {
        req.destroy();
        reject(new Error('Request timed out'));
      });
    });
  }

  public unsubscribe() {
    if (this.sseRequest) {
      this.sseRequest.destroy();
      this.sseRequest = null;
    }
  }
}
