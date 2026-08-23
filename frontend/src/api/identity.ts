export interface BindingCredential {
  token: string;
  expiresInSeconds: number;
}

export async function createBindingCredential(): Promise<BindingCredential> {
  return { token: "WS-8K7M-2Q9P", expiresInSeconds: 900 };
}
