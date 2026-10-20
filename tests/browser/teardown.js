import { mkdir, writeFile } from "node:fs/promises";
export default async function teardown() {
  await mkdir(".runtime", { recursive: true });
  await writeFile(".runtime/e2e-stop", "stop owned development children");
}
