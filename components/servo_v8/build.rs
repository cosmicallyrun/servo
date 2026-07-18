/* This Source Code Form is subject to the terms of the Mozilla Public
 * License, v. 2.0. If a copy of the MPL was not distributed with this
 * file, You can obtain one at https://mozilla.org/MPL/2.0/. */

use std::env;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

fn required(path: &Path, description: &str) {
    assert!(
        path.exists(),
        "missing {description} at {}; build V8 using support/v8/README.md first",
        path.display()
    );
}

fn try_python(program: &str, arguments: &[&str]) -> bool {
    Command::new(program)
        .args(arguments)
        .arg("--version")
        .stdin(Stdio::null())
        .output()
        .is_ok_and(|output| output.status.success())
}

fn find_python() -> Command {
    if let Some(program) = env::var_os("PYTHON") {
        return Command::new(program);
    }
    if try_python("uv", &["run", "--frozen", "python"]) {
        let mut command = Command::new("uv");
        command.args(["run", "--frozen", "python"]);
        return command;
    }
    if try_python("python3", &[]) {
        return Command::new("python3");
    }
    if try_python("python", &[]) {
        return Command::new("python");
    }
    panic!("no suitable Python interpreter found for V8 WebIDL generation");
}

fn main() {
    println!("cargo:rerun-if-changed=include/servo_v8.h");
    println!("cargo:rerun-if-changed=include/servo_v8.exports");
    println!("cargo:rerun-if-changed=src/bridge.cc");
    println!("cargo:rerun-if-changed=codegen/generate.py");
    println!("cargo:rerun-if-changed=codegen/generate_tests.py");
    println!("cargo:rerun-if-changed=codegen/generate_document_host.py");
    println!("cargo:rerun-if-changed=codegen/generate_document_host_tests.py");
    println!("cargo:rerun-if-changed=codegen/production_webidl.py");
    println!("cargo:rerun-if-changed=codegen/production_webidl_tests.py");
    println!("cargo:rerun-if-changed=webidls/EngineBindingSmoke.webidl");
    println!("cargo:rerun-if-changed=../script_bindings/webidls");
    println!("cargo:rerun-if-changed=../script_bindings/third_party/WebIDL/parser");
    println!("cargo:rerun-if-changed=../script_bindings/third_party/ply");
    println!("cargo:rerun-if-env-changed=SERVO_V8_ROOT");
    println!("cargo:rerun-if-env-changed=SERVO_V8_OUT_DIR");
    println!("cargo:rerun-if-env-changed=PYTHON");

    let target = env::var("TARGET").expect("Cargo did not provide TARGET");
    assert_eq!(
        target, "aarch64-apple-darwin",
        "servo-v8 currently supports only aarch64-apple-darwin; Linux ARM64 support is deferred"
    );

    let manifest_dir = PathBuf::from(env::var_os("CARGO_MANIFEST_DIR").unwrap());
    let out_dir = PathBuf::from(env::var_os("OUT_DIR").unwrap());
    let servo_root = manifest_dir.ancestors().nth(2).unwrap();
    let generator = manifest_dir.join("codegen/generate.py");
    let webidl = manifest_dir.join("webidls/EngineBindingSmoke.webidl");
    let status = find_python()
        .arg(&generator)
        .arg(&webidl)
        .arg(&out_dir)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .status()
        .unwrap_or_else(|error| panic!("failed to run {}: {error}", generator.display()));
    assert!(status.success(), "V8 WebIDL generation failed");

    let document_host_generator = manifest_dir.join("codegen/generate_document_host.py");
    let production_webidls = servo_root.join("components/script_bindings/webidls");
    let status = find_python()
        .arg(&document_host_generator)
        .arg(&production_webidls)
        .arg(&out_dir)
        .env("PYTHONDONTWRITEBYTECODE", "1")
        .status()
        .unwrap_or_else(|error| {
            panic!(
                "failed to run {}: {error}",
                document_host_generator.display()
            )
        });
    assert!(status.success(), "V8 Document host generation failed");

    let default_v8_root = servo_root.parent().unwrap().join("v8");
    let v8_root = env::var_os("SERVO_V8_ROOT")
        .map(PathBuf::from)
        .unwrap_or(default_v8_root);
    let v8_out = env::var_os("SERVO_V8_OUT_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| v8_root.join("out/servo-v8"));

    let monolith = v8_out.join("obj/libv8_monolith.a");
    let libcxx_dir = v8_out.join("obj/buildtools/third_party/libc++");
    let libcxxabi_dir = v8_out.join("obj/buildtools/third_party/libc++abi");
    required(&monolith, "V8 monolith");
    required(&libcxx_dir.join("libc++.a"), "V8 custom libc++");
    required(&libcxxabi_dir.join("libc++abi.a"), "V8 custom libc++abi");
    required(
        &v8_out.join("gen/include/v8-gn.h"),
        "generated V8 ABI header",
    );
    println!("cargo:rerun-if-changed={}", monolith.display());
    println!(
        "cargo:rerun-if-changed={}",
        libcxx_dir.join("libc++.a").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        libcxxabi_dir.join("libc++abi.a").display()
    );
    println!(
        "cargo:rerun-if-changed={}",
        v8_out.join("gen/include/v8-gn.h").display()
    );

    let llvm_root = v8_root.join("third_party/llvm-build/Release+Asserts");
    let clang = llvm_root.join("bin/clang++");
    let lld = llvm_root.join("bin/ld64.lld");
    required(&clang, "V8 pinned clang++");
    required(&lld, "V8 pinned ld64.lld");
    let clang_lib = llvm_root.join("lib/clang");
    let clang_rt = std::fs::read_dir(&clang_lib)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", clang_lib.display()))
        .filter_map(Result::ok)
        .map(|entry| entry.path().join("lib/darwin/libclang_rt.osx.a"))
        .find(|path| path.exists())
        .unwrap_or_else(|| panic!("missing libclang_rt.osx.a under {}", clang_lib.display()));
    println!("cargo:rerun-if-changed={}", clang_rt.display());
    let sdk_links = v8_out.join("sdk/xcode_links");
    let sdk = std::fs::read_dir(&sdk_links)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", sdk_links.display()))
        .filter_map(Result::ok)
        .map(|entry| entry.path())
        .find(|path| {
            path.file_name()
                .and_then(|name| name.to_str())
                .is_some_and(|name| name.starts_with("MacOSX") && name.ends_with(".sdk"))
        })
        .unwrap_or_else(|| panic!("missing V8-linked macOS SDK under {}", sdk_links.display()));

    let mut build = cc::Build::new();
    build
        .cargo_metadata(false)
        .cpp(true)
        .cpp_link_stdlib(None)
        .compiler(&clang)
        .file("src/bridge.cc")
        .include("include")
        .include(&out_dir)
        .include(&v8_root)
        .include(v8_root.join("include"))
        .include(v8_out.join("gen"))
        .include(v8_out.join("gen/include"))
        .include(v8_root.join("buildtools/third_party/libc++"))
        .flag("-std=c++20")
        .flag("-fno-exceptions")
        .flag("-fno-rtti")
        .flag("--target=arm64-apple-macos")
        .flag("-mmacos-version-min=13.0")
        .flag("-isysroot")
        .flag(sdk.to_str().unwrap())
        .flag("-nostdinc++")
        .flag(format!(
            "-isystem{}",
            v8_root.join("third_party/libc++/src/include").display()
        ))
        .flag(format!(
            "-isystem{}",
            v8_root.join("third_party/libc++abi/src/include").display()
        ))
        .define("V8_GN_HEADER", None)
        .define("_LIBCPP_HARDENING_MODE", "_LIBCPP_HARDENING_MODE_EXTENSIVE")
        .define("_LIBCPP_DISABLE_VISIBILITY_ANNOTATIONS", None)
        .define("_LIBCXXABI_DISABLE_VISIBILITY_ANNOTATIONS", None)
        .flag_if_supported("-Wno-unused-parameter")
        .flag_if_supported("-Wno-cast-function-type-mismatch")
        .warnings(true)
        .extra_warnings(true)
        .compile("servo_v8_bridge");

    // SpiderMonkey vendors irregexp under v8::internal, and Servo's other C++
    // dependencies use Apple's libc++. Keep full V8 and Chromium's namespaced
    // libc++ in a dylib that exports only the experimental Servo C ABI.
    let bridge_archive = out_dir.join("libservo_v8_bridge.a");
    let dylib = out_dir.join("libservo_v8_bridge.dylib");
    let exports = manifest_dir.join("include/servo_v8.exports");
    required(&bridge_archive, "compiled Servo V8 C++ bridge");
    required(&exports, "Servo V8 exported-symbol list");
    let status = Command::new(&clang)
        .arg("--target=arm64-apple-macos")
        .arg("-mmacos-version-min=13.0")
        .arg("-isysroot")
        .arg(&sdk)
        .arg(format!("-fuse-ld={}", lld.display()))
        .arg("-dynamiclib")
        .arg("-nostdlib++")
        .arg(format!("-Wl,-force_load,{}", bridge_archive.display()))
        .arg(&monolith)
        .arg(libcxx_dir.join("libc++.a"))
        .arg(libcxxabi_dir.join("libc++abi.a"))
        .arg(&clang_rt)
        .args(["-framework", "Foundation"])
        .arg(format!("-Wl,-exported_symbols_list,{}", exports.display()))
        .arg("-Wl,-install_name,@rpath/libservo_v8_bridge.dylib")
        // Keep these in sync with SERVO_V8_ABI_VERSION in servo_v8.h.
        .args([
            "-Wl,-compatibility_version,7.0.0",
            "-Wl,-current_version,7.0.0",
        ])
        .arg("-o")
        .arg(&dylib)
        .status()
        .unwrap_or_else(|error| panic!("failed to link {}: {error}", dylib.display()));
    assert!(status.success(), "failed to link {}", dylib.display());

    println!("cargo:rustc-link-search=native={}", out_dir.display());
    println!("cargo:rustc-link-lib=dylib=servo_v8_bridge");
    println!("cargo:rustc-link-arg=-Wl,-rpath,{}", out_dir.display());
    println!("cargo:dylib_path={}", dylib.display());
}
