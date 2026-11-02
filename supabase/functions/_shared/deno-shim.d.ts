// 仅用于本地 tsc 静态检查的 Deno 环境声明（不参与 Supabase 部署）。

declare namespace Deno {
  function serve(handler: (req: Request) => Response | Promise<Response>): void;
  namespace env {
    function get(key: string): string | undefined;
  }
}

declare module "npm:mysql2@3.24.6/promise" {
  interface Connection {
    query(sql: string, params?: unknown[]): Promise<[unknown, unknown]>;
    execute(sql: string, params?: unknown[]): Promise<[unknown, unknown]>;
    end(): Promise<void>;
  }
  interface Mysql {
    createConnection(config: Record<string, unknown>): Promise<Connection>;
  }
  const mysql: Mysql;
  export default mysql;
}
