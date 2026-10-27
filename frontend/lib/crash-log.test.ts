import { describe, expect, it } from "vitest";
import { CRASH_MAX_ENTRIES, readCrashes, recordCrash } from "./crash-log";

function memoryStorage() {
  const data = new Map<string, string>();
  return { getItem: (key: string) => data.get(key) ?? null, setItem: (key: string, value: string) => void data.set(key, value) };
}

describe("crash log", () => {
  it("keeps the message, stack and digest of each crash, newest last, at most ten", () => {
    const storage = memoryStorage();
    for (let index = 0; index < CRASH_MAX_ENTRIES + 2; index += 1) {
      recordCrash(Object.assign(new Error(`boom ${index}`), { digest: "d1" }), storage, new Date(Date.UTC(2026, 9, 27, 15, index)));
    }
    const crashes = readCrashes(storage);
    expect(crashes).toHaveLength(CRASH_MAX_ENTRIES);
    expect(crashes.at(-1)?.message).toBe(`boom ${CRASH_MAX_ENTRIES + 1}`);
    expect(crashes.at(-1)?.digest).toBe("d1");
    expect(crashes.at(-1)?.stack).toContain("boom");
  });

  it("never throws when storage is broken", () => {
    const broken = { getItem: () => { throw new Error("blocked"); }, setItem: () => { throw new Error("blocked"); } };
    expect(() => recordCrash(new Error("x"), broken)).not.toThrow();
    expect(readCrashes(broken)).toEqual([]);
  });
});
