// 仅用于本地 tsc 静态检查的 Deno 环境声明（不参与 Supabase 部署）。
declare namespace Deno {
  function serve(handler: (req: Request) => Response | Promise<Response>): void;
  namespace env {
    function get(key: string): string | undefined;
  }
}

declare module "https://deno.land/x/mysql@v2.12.1/mod.ts" {
  export class Client {
    connect(config: Record<string, unknown>): Promise<Client>;
    query(sql: string, params?: unknown[]): Promise<unknown[]>;
    execute(sql: string, params?: unknown[]): Promise<unknown>;
    close(): Promise<void>;
  }
}
