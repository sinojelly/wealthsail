// Preserve the native vendor distribution, including its companion binaries,
// without shipping npm, Node or the JavaScript launcher in the runtime image.
import {
  closeSync,
  cpSync,
  existsSync,
  mkdirSync,
  openSync,
  readSync,
  writeFileSync,
} from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function linuxTarget(platform = process.platform, arch = process.arch) {
  if (platform !== "linux") {
    throw new Error(`Codex runtime requires Linux, received ${platform}`);
  }
  const supported = {
    x64: {
      packageName: "@openai/codex-linux-x64",
      triple: "x86_64-unknown-linux-musl",
      machine: 62,
    },
    arm64: {
      packageName: "@openai/codex-linux-arm64",
      triple: "aarch64-unknown-linux-musl",
      machine: 183,
    },
  };
  if (!Object.hasOwn(supported, arch)) {
    throw new Error(`Unsupported Codex runtime architecture: ${arch}`);
  }
  return supported[arch];
}

function verifyNativeExecutable(filename, machine) {
  const handle = openSync(filename, "r");
  const header = Buffer.alloc(20);
  let size;
  try {
    size = readSync(handle, header, 0, header.length, 0);
  } finally {
    closeSync(handle);
  }
  // Both supported targets are ELF64 little-endian. Reject a JS wrapper or a
  // mismatched optional package before the image build can silently succeed.
  if (
    size !== header.length ||
    !header.subarray(0, 4).equals(Buffer.from([0x7f, 0x45, 0x4c, 0x46])) ||
    header[4] !== 2 ||
    header[5] !== 1 ||
    header.readUInt16LE(18) !== machine
  ) {
    throw new Error("Codex distribution does not contain the expected native ELF");
  }
}

export function extractNative(packageRoot, output, platform, arch) {
  const target = linuxTarget(platform, arch);
  const roots = [];
  const require = createRequire(path.join(path.resolve(packageRoot), "package.json"));
  try {
    const nativePackage = require.resolve(`${target.packageName}/package.json`);
    roots.push(path.join(path.dirname(nativePackage), "vendor", target.triple));
  } catch {
    // Older official distributions placed vendor directly in @openai/codex.
  }
  roots.push(path.join(packageRoot, "vendor", target.triple));

  let selected;
  for (const root of roots) {
    for (const relative of ["bin/codex", "codex/codex"]) {
      const filename = path.join(root, relative);
      if (existsSync(filename)) {
        verifyNativeExecutable(filename, target.machine);
        selected = { root, relative };
        break;
      }
    }
    if (selected) break;
  }
  if (!selected) {
    throw new Error(`Missing native Codex distribution for ${target.triple}`);
  }

  mkdirSync(output, { recursive: true });
  cpSync(selected.root, path.join(output, "native"), {
    recursive: true,
    errorOnExist: true,
    force: false,
    preserveTimestamps: true,
  });
  writeFileSync(path.join(output, "native-executable"), `${selected.relative}\n`);
  return selected.relative;
}

if (
  process.argv[1] &&
  path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const [, , packageRoot, output] = process.argv;
  if (!packageRoot || !output) {
    throw new Error("Usage: extract-native.mjs PACKAGE_ROOT OUTPUT_DIRECTORY");
  }
  extractNative(packageRoot, output);
}
