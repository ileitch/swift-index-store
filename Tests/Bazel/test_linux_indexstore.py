"""Exercise LinuxIndexStore linking and runfiles using a synthetic Swift toolchain."""

import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform.startswith("linux"), "requires an ELF linker and loader")
class LinuxIndexStoreTest(unittest.TestCase):
    def test_runtime_library_names(self):
        source_root = Path(__file__).resolve().parents[2]
        bazel = shutil.which("bazel") or shutil.which("bazelisk")
        self.assertIsNotNone(bazel, "bazel or bazelisk must be on PATH")
        compiler = shlex.split(os.environ.get("CC", "cc"))
        env = os.environ.copy()
        env.setdefault("USE_BAZEL_VERSION", (source_root / ".bazelversion").read_text().strip())
        env.pop("LD_LIBRARY_PATH", None)
        env.pop("LD_PRELOAD", None)

        with tempfile.TemporaryDirectory(prefix="indexstore-soname-") as temporary:
            root = Path(temporary)
            workspace = root / "workspace"
            workspace.mkdir()
            (workspace / "MODULE.bazel").write_text(
                'module(name = "indexstore_soname_test")\n'
                'bazel_dep(name = "rules_cc", version = "0.2.17")\n'
                'bazel_dep(name = "swift-index-store", version = "1.10.0")\n'
                'local_path_override(module_name = "swift-index-store", '
                f'path = "{source_root.as_posix()}")\n'
                'deps = use_extension("@swift-index-store//:repositories.bzl", "bzlmod_deps")\n'
                'use_repo(deps, "LinuxIndexStore")\n'
            )
            (workspace / "BUILD.bazel").write_text(
                'load("@rules_cc//cc:defs.bzl", "cc_binary")\n'
                'cc_binary(name = "probe", srcs = ["main.c"], '
                'deps = ["@LinuxIndexStore//:libIndexStore"])\n'
            )
            (workspace / "main.c").write_text(
                "extern int indexstore_soname_test(void);\n"
                "int main(void) { return indexstore_soname_test() == 42 ? 0 : 1; }\n"
            )
            library_source = root / "library.c"
            library_source.write_text("int indexstore_soname_test(void) { return 42; }\n")

            cases = [
                ("unversioned", "libIndexStore.so", None),
                ("versioned_without_soname", "libIndexStore.so.1.2.3", None),
                ("versioned", "libIndexStore.so.1", "libIndexStore.so.1"),
                ("different_soname", "libIndexStore.so.1.2.3", "libIndexStore.so.1"),
            ]
            for case, filename, soname in cases:
                with self.subTest(case=case):
                    toolchain = root / case
                    bin_dir = toolchain / "bin"
                    lib_dir = toolchain / "lib"
                    bin_dir.mkdir(parents=True)
                    lib_dir.mkdir()
                    swiftc = bin_dir / "swiftc"
                    swiftc.write_text("#!/bin/sh\nexit 1\n")
                    swiftc.chmod(0o755)
                    command = compiler + ["-shared", "-fPIC", str(library_source), "-o", str(lib_dir / filename)]
                    if soname:
                        command.append(f"-Wl,-soname,{soname}")
                    subprocess.run(command, check=True, env=env)
                    if soname and soname != filename:
                        (lib_dir / soname).symlink_to(filename)
                    if filename != "libIndexStore.so":
                        (lib_dir / "libIndexStore.so").symlink_to(soname or filename)
                    subprocess.run(
                        [
                            bazel,
                            "--batch",
                            "--ignore_all_rc_files",
                            f"--output_user_root={root / 'bazel'}",
                            "run",
                            "--enable_bzlmod",
                            "--enable_workspace=false",
                            f"--repo_env=PATH={bin_dir}{os.pathsep}{env['PATH']}",
                            "//:probe",
                        ],
                        cwd=workspace,
                        env=env,
                        check=True,
                    )


if __name__ == "__main__":
    unittest.main()
