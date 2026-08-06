import { helper } from "./helper.js";

export class Service {
  greet(value: string): string {
    return helper(value);
  }
}

export function run(value: string): string {
  const service = new Service();
  return service.greet(value);
}
