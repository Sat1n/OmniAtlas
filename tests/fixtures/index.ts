import { createUser } from "./api";

export const API_BASE = "/api";

export class UserClient {
  async list(): Promise<Response> {
    return fetch("/api/hello");
  }
}

export function submit(): void {
  fetch("/api/users", { method: "POST" });
}

export function missingBackend(): void {
  fetch("/api/unknown-endpoint");
}
