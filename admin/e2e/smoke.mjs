// End-to-end smoke test of the admin UI against a running server that serves it at /ui.
// Usage: LIVEOPS_URL=http://localhost:8000 LIVEOPS_ADMIN_TOKEN=... node e2e/smoke.mjs [screenshotDir]
import { createRequire } from "node:module";

// CommonJS require so NODE_PATH / a globally installed playwright works too.
const { chromium } = createRequire(import.meta.url)("playwright");

const base = process.env.LIVEOPS_URL ?? "http://localhost:8000";
const token = process.env.LIVEOPS_ADMIN_TOKEN ?? "dev-admin-token";
const shots = process.argv[2];
const slug = `e2e-${Date.now()}`;

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 820 } });
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
page.on("console", (m) => m.type() === "error" && !/status of (401|404)/.test(m.text()) && errors.push(m.text()));
const shot = async (name) => shots && (await page.screenshot({ path: `${shots}/${name}.png`, fullPage: true }));
const step = (name) => console.log(`• ${name}`);

try {
  step("login rejects a bad token");
  await page.goto(`${base}/ui/`);
  await page.getByRole("button", { name: "Use admin token instead" }).click();
  await page.getByLabel("Admin token").fill("wrong");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByText("invalid admin token").waitFor();

  step("login");
  await page.getByLabel("Admin token").fill(token);
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("heading", { name: "Projects", exact: true }).waitFor();

  step("create project");
  await page.getByRole("button", { name: "New project" }).first().click();
  await page.getByLabel("Name").fill(`E2E ${slug}`);
  await page.getByLabel("Slug").fill(slug);
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("heading", { name: `E2E ${slug}` }).waitFor();
  await shot("project");

  step("open dev environment");
  await page.getByRole("link", { name: /^dev/ }).click();
  await page.getByRole("heading", { name: "Remote config" }).waitFor();

  step("publish config");
  await page.getByLabel("Config JSON").fill('{"lives": 3, "shop": {"discount": 0.2}}');
  await page.getByLabel("Change note").fill("initial");
  await page.getByRole("button", { name: "Publish v1" }).click();
  await page.getByText("Published v1").waitFor();
  await page.getByLabel("Config JSON").fill('{"lives": 5}');
  await page.getByRole("button", { name: "Publish v2" }).click();
  await page.getByText("Published v2").waitFor();
  await page.getByLabel("Config JSON").fill("{ broken");
  await page.getByText(/Invalid JSON/).waitFor();
  await page.getByRole("button", { name: "Discard changes" }).click();
  await shot("config");

  step("rollback to v1");
  page.once("dialog", (d) => d.accept());
  await page.getByRole("row", { name: /v1 initial/ }).getByRole("button", { name: "View" }).click();
  await page.getByRole("button", { name: "Roll back to this version" }).click();
  await page.getByText("Rolled back — now v3").waitFor();

  step("schedule a publish");
  await page.getByLabel("Config JSON").fill('{"event": "halloween"}');
  await page.getByRole("button", { name: "Schedule…" }).click();
  await page.getByRole("button", { name: "Schedule", exact: true }).click();
  await page.getByText(/Scheduled for/).first().waitFor();
  await page.getByRole("heading", { name: "Scheduled" }).waitFor();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Cancel", exact: true }).click();
  await page.getByText("Schedule cancelled").waitFor();

  step("create and start an experiment");
  await page.getByRole("link", { name: "Experiments" }).click();
  await page.getByRole("button", { name: "New experiment" }).click();
  await page.getByRole("dialog").getByRole("textbox", { name: /^Key/ }).fill("shop_price");
  await page.getByLabel("Overrides for treatment").fill('{"shop": {"discount": 0.5}}');
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Experiment created").waitFor();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Start" }).click();
  await page.getByText("shop_price running").waitFor();
  await shot("experiments");

  step("add a webhook");
  await page.getByRole("link", { name: "Webhooks" }).click();
  await page.getByRole("button", { name: "Add webhook" }).click();
  await page.getByLabel("Endpoint URL").fill("https://hooks.example.com/liveops");
  await page.getByRole("button", { name: "Create" }).click();
  const whsec = (await page.locator(".secret").innerText()).trim();
  if (!whsec.startsWith("whsec_")) throw new Error(`unexpected secret ${whsec}`);
  await page.getByRole("button", { name: "Done" }).click();
  await page.getByRole("button", { name: "Deliveries" }).click();
  await page.getByRole("heading", { name: "Recent deliveries" }).waitFor();
  await page.keyboard.press("Escape");
  await shot("webhooks");

  step("create flag with rule");
  await page.getByRole("link", { name: "Feature flags" }).click();
  await page.getByRole("button", { name: "New flag" }).click();
  await page.getByLabel("Key").fill("new_shop");
  await page.getByText("Disabled", { exact: true }).click();
  await page.getByRole("button", { name: "Add rule" }).click();
  await page.getByLabel("Value").fill("ios");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Flag created").waitFor();
  await page.getByText("platform equals ios").waitFor();
  await page.getByLabel("Toggle new_shop").click();
  await page.getByText("new_shop disabled").waitFor();
  await shot("flags");

  step("create API key");
  await page.getByRole("link", { name: "API keys" }).click();
  await page.getByRole("button", { name: "New key" }).click();
  await page.getByLabel("Name").fill("e2e build");
  await page.getByRole("button", { name: "Create" }).click();
  const key = (await page.locator(".secret").innerText()).trim();
  if (!key.startsWith("lo_client_")) throw new Error(`unexpected key ${key}`);
  await page.getByRole("button", { name: "Done" }).click();

  step("create leaderboard and see a score from a real player");
  await page.getByRole("link", { name: "Leaderboards" }).click();
  await page.getByRole("button", { name: "New leaderboard" }).first().click();
  await page.getByLabel("Name").fill("Main");
  await page.getByLabel("Key").fill("main");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByRole("heading", { name: /Main/ }).waitFor();

  const login = await (await fetch(`${base}/v1/auth/anonymous`, {
    method: "POST",
    headers: { "X-Api-Key": key, "Content-Type": "application/json" },
    body: JSON.stringify({ device_id: `e2e-device-${slug}` }),
  })).json();
  const auth = { Authorization: `Bearer ${login.access_token}`, "Content-Type": "application/json" };
  await fetch(`${base}/v1/players/me`, { method: "PATCH", headers: auth, body: '{"display_name": "E2E Hero"}' });
  await fetch(`${base}/v1/leaderboards/main/scores`, { method: "POST", headers: auth, body: '{"score": 1234}' });
  await fetch(`${base}/v1/saves/main`, { method: "PUT", headers: auth, body: '{"data": {"gold": 42}}' });

  await page.reload();
  await page.getByRole("cell", { name: "1,234" }).waitFor();
  await page.getByText("E2E Hero").waitFor();

  step("configure anti-cheat validation");
  await page.getByRole("button", { name: "Anti-cheat" }).click();
  await page.getByLabel("Max score").fill("100000");
  await page.getByLabel("Submits / min / player").fill("30");
  await page.getByLabel(/Validation hook URL/).fill("https://anticheat.example.com/validate");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Anti-cheat settings saved").waitFor();
  await page.getByText("validated", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Anti-cheat" }).click();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Rotate signing secret" }).click();
  if (!(await page.locator(".secret").innerText()).startsWith("vsec_")) throw new Error("no validation secret");
  await page.keyboard.press("Escape");
  await shot("leaderboard");

  step("configure sign-in providers");
  await page.getByRole("link", { name: "Player sign-in" }).click();
  await page.getByLabel("OAuth client IDs").fill("abc.apps.googleusercontent.com");
  await page.getByLabel("App ID").fill("480");
  await page.getByLabel("Publisher Web API key").fill("steam-secret-9876");
  await page.getByRole("button", { name: "Save" }).click();
  await page.getByText("Sign-in providers saved").waitFor();
  if ((await page.getByLabel("Publisher Web API key").inputValue()) !== "••••9876") throw new Error("steam key not masked");

  step("inspect player saves");
  await page.getByRole("link", { name: "Players & saves" }).click();
  await page.getByLabel("Search players").fill(`e2e-device-${slug}`);
  await page.getByRole("button", { name: "Search" }).click();
  await page.getByRole("button", { name: "Saves" }).click();
  await page.getByRole("button", { name: "View" }).click();
  await page.getByText('"gold": 42').waitFor();
  await shot("saves");
  await page.keyboard.press("Escape");

  step("mobile layout");
  await page.setViewportSize({ width: 390, height: 800 });
  await page.getByRole("button", { name: "Open menu" }).click();
  await page.getByRole("link", { name: "Remote config" }).click();
  await page.getByRole("heading", { name: "Remote config" }).waitFor();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  if (overflow) throw new Error("horizontal overflow on mobile");
  await shot("mobile");

  step("delete project");
  await page.setViewportSize({ width: 1280, height: 820 });
  await page.getByRole("link", { name: `E2E ${slug}` }).click();
  page.once("dialog", (d) => d.accept());
  await page.getByRole("button", { name: "Delete project" }).click();
  await page.getByText("Project deleted").waitFor();

  step("invite a viewer, check audit log");
  await page.getByRole("link", { name: "Admin users" }).click();
  await page.getByRole("button", { name: "Invite user" }).click();
  await page.getByLabel("Email", { exact: true }).fill(`viewer-${slug}@example.com`);
  await page.getByLabel("Initial password").fill("viewer-password-1");
  await page.getByRole("dialog").locator("select").selectOption("viewer");
  await page.getByRole("button", { name: "Create" }).click();
  await page.getByText(`viewer-${slug}@example.com`).waitFor();
  await page.getByRole("link", { name: "Audit log" }).click();
  await page.getByText("/admin/users").first().waitFor();
  await shot("audit");

  step("viewer is read-only");
  await page.getByRole("button", { name: "Sign out" }).click();
  await page.getByLabel("Email", { exact: true }).fill(`viewer-${slug}@example.com`);
  await page.getByLabel("Password").fill("viewer-password-1");
  await page.getByRole("button", { name: "Sign in", exact: true }).click();
  await page.getByRole("heading", { name: "Projects", exact: true }).waitFor();
  if (!(await page.getByRole("button", { name: "New project" }).first().isDisabled()))
    throw new Error("viewer can create projects");
  if (await page.getByRole("link", { name: "Admin users" }).count()) throw new Error("viewer sees users");

  if (errors.length) throw new Error(`browser errors:\n${errors.join("\n")}`);
  console.log("✓ admin UI smoke test passed");
} catch (e) {
  await shot("failure");
  console.error(e);
  process.exitCode = 1;
} finally {
  await browser.close();
}
