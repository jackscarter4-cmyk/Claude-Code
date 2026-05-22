import type { NormalizedLandmark } from "@mediapipe/tasks-vision";

export type Frame = {
  t_ms: number;
  landmarks: NormalizedLandmark[];
};

export type SwingRecord = {
  key: string;
  fileName: string;
  fileSize: number;
  durationMs: number;
  frames: Frame[];
  savedAt: number;
};

const DB_NAME = "swing-coach";
const DB_VERSION = 1;
const STORE = "swings";

function openDB(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB_NAME, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains(STORE)) {
        db.createObjectStore(STORE, { keyPath: "key" });
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export function makeKey(fileName: string, fileSize: number): string {
  return `${fileName}__${fileSize}`;
}

export async function saveSwing(record: SwingRecord): Promise<void> {
  const db = await openDB();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).put(record);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}

export async function loadSwing(key: string): Promise<SwingRecord | null> {
  const db = await openDB();
  const result = await new Promise<SwingRecord | null>((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).get(key);
    req.onsuccess = () => resolve((req.result as SwingRecord) ?? null);
    req.onerror = () => reject(req.error);
  });
  db.close();
  return result;
}

export async function listSwings(): Promise<SwingRecord[]> {
  const db = await openDB();
  const result = await new Promise<SwingRecord[]>((resolve, reject) => {
    const tx = db.transaction(STORE, "readonly");
    const req = tx.objectStore(STORE).getAll();
    req.onsuccess = () => resolve((req.result as SwingRecord[]) ?? []);
    req.onerror = () => reject(req.error);
  });
  db.close();
  return result.sort((a, b) => b.savedAt - a.savedAt);
}

export async function deleteSwing(key: string): Promise<void> {
  const db = await openDB();
  await new Promise<void>((resolve, reject) => {
    const tx = db.transaction(STORE, "readwrite");
    tx.objectStore(STORE).delete(key);
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}
