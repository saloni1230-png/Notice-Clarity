import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";

test("English workspace: upload, evidence, context, question, export and deletion", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "A notice arrived? Let's make sense of it.",
    }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Upload notice", exact: true })
    .click();
  await page
    .getByLabel("Email", { exact: true })
    .fill(`browser-${Date.now()}@example.com`);
  await page
    .getByLabel("Password", { exact: true })
    .fill("browser-strong-password-123");
  await page
    .getByRole("button", { name: "Create account", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "Sign out", exact: true }),
  ).toBeVisible();
  await page
    .locator("#file-input")
    .setInputFiles(path.resolve("notice/web/sample.pdf"));
  await expect(
    page.getByRole("heading", { name: "sample.pdf", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("INR 12,500.00", { exact: true })).toBeVisible({
    timeout: 30000,
  });
  await page
    .getByRole("button", { name: "View page evidence ↗" })
    .first()
    .click();
  await expect(page.locator(".highlight").first()).toBeVisible();
  await page.getByRole("button", { name: "Context", exact: true }).click();
  await page.getByLabel("State / relevant office").fill("Delhi");
  await page
    .getByLabel("What other context should we know?")
    .fill("<img src=x onerror=alert(1)>");
  await page.getByRole("button", { name: "Save context" }).click();
  await expect(page.getByRole("status")).toContainText("Context saved");
  await page.getByRole("button", { name: "Next steps", exact: true }).click();
  await page.getByRole("checkbox").first().check();
  await page.getByRole("button", { name: "Questions", exact: true }).click();
  await page
    .getByRole("textbox", { name: "Your question" })
    .fill("What amount is mentioned?");
  await page.getByRole("button", { name: "Ask ↗", exact: true }).click();
  await expect(
    page.getByText("Document facts · original evidence linked"),
  ).toBeVisible({ timeout: 30000 });
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export ↓" }).click();
  expect((await downloadPromise).suggestedFilename()).toBe(
    "notice-workspace.json",
  );
  const axe = await new AxeBuilder({ page }).analyze();
  expect(
    axe.violations.filter((v) => ["critical", "serious"].includes(v.impact)),
  ).toEqual([]);
  expect(errors).toEqual([]);
  await page.screenshot({
    path: "test-results/workspace-desktop.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Delete", exact: true }).click();
  await page.getByRole("button", { name: "Yes, delete this case" }).click();
  await expect(
    page.getByRole("heading", {
      name: "A notice arrived? Let's make sense of it.",
    }),
  ).toBeVisible();
});

test("mobile onboarding, help and source transparency", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(
    page.getByRole("heading", {
      name: "A notice arrived? Let's make sense of it.",
    }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Help", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText(
    "How to use Notice Clarity",
  );
  await page.getByRole("button", { name: "Close" }).click();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
  await page.screenshot({
    path: "test-results/home-mobile.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Official sources", exact: true })
    .click();
  await expect(
    page.getByText("No sources have been ingested and approved.", {
      exact: false,
    }),
  ).toBeVisible();
  expect(
    (await new AxeBuilder({ page }).analyze()).violations.filter((v) =>
      ["serious", "critical"].includes(v.impact),
    ),
  ).toEqual([]);
});
