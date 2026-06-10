"""
encoder_decoder.py — Split a zip file into 15MB base64 .txt chunks for transfer,
and reconstruct the original zip from those chunks on another machine.

Usage:
  Split:       python encoder_decoder.py split <file.zip> [output_dir]
  Reconstruct: python encoder_decoder.py reconstruct <chunk_dir> <output.zip>
"""

import os
import sys
import base64
import hashlib
import json


# ~15 MB of raw bytes per chunk (base64 output will be ~20 MB of text)
CHUNK_SIZE_BYTES = 15 * 1024 * 1024


def compute_sha256(data: bytes) -> str:
    """Return hex SHA-256 digest of a byte string."""
    return hashlib.sha256(data).hexdigest()


def split_zip(zip_path: str, output_dir: str) -> None:
    """
    Read a zip file and write it out as a series of base64-encoded .txt chunk
    files, plus a manifest.json describing how to reassemble them.

    Each chunk file is named:  <stem>_part_001.txt, _part_002.txt, ...
    The manifest is named:     <stem>_manifest.json
    """
    if not os.path.isfile(zip_path):
        raise FileNotFoundError(f"Input file not found: {zip_path}")

    os.makedirs(output_dir, exist_ok=True)

    stem = os.path.splitext(os.path.basename(zip_path))[0]

    with open(zip_path, "rb") as fh:
        raw = fh.read()

    total_bytes = len(raw)
    file_sha256 = compute_sha256(raw)

    # Slice into chunks and encode each one as base64 text
    chunks = []
    part = 1
    offset = 0

    while offset < total_bytes:
        chunk_data = raw[offset : offset + CHUNK_SIZE_BYTES]
        encoded = base64.b64encode(chunk_data).decode("ascii")
        chunk_sha256 = compute_sha256(chunk_data)

        filename = f"{stem}_part_{part:03d}.txt"
        filepath = os.path.join(output_dir, filename)

        with open(filepath, "w", encoding="ascii") as cf:
            cf.write(encoded)

        chunks.append({"part": part, "filename": filename, "chunk_sha256": chunk_sha256})
        print(f"  Wrote {filepath}  ({len(chunk_data):,} raw bytes)")

        offset += CHUNK_SIZE_BYTES
        part += 1

    # Write the manifest so reconstruction is self-contained
    manifest = {
        "original_filename": os.path.basename(zip_path),
        "total_bytes": total_bytes,
        "file_sha256": file_sha256,
        "total_parts": len(chunks),
        "chunks": chunks,
    }
    manifest_path = os.path.join(output_dir, f"{stem}_manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as mf:
        json.dump(manifest, mf, indent=2)

    print(f"\nSplit complete.")
    print(f"  Original file : {zip_path}")
    print(f"  Total size    : {total_bytes:,} bytes")
    print(f"  Chunks written: {len(chunks)}")
    print(f"  Manifest      : {manifest_path}")
    print(f"  File SHA-256  : {file_sha256}")


def reconstruct_zip(chunk_dir: str, output_path: str) -> None:
    """
    Read the manifest from chunk_dir, decode and concatenate every chunk in
    order, verify checksums, and write the reconstructed zip to output_path.
    """
    # Locate the manifest (there should be exactly one)
    manifests = [f for f in os.listdir(chunk_dir) if f.endswith("_manifest.json")]
    if not manifests:
        raise FileNotFoundError(f"No *_manifest.json found in: {chunk_dir}")
    if len(manifests) > 1:
        raise ValueError(
            f"Multiple manifests found; please specify which to use: {manifests}"
        )

    manifest_path = os.path.join(chunk_dir, manifests[0])
    with open(manifest_path, "r", encoding="utf-8") as mf:
        manifest = json.load(mf)

    expected_total = manifest["total_bytes"]
    expected_file_sha256 = manifest["file_sha256"]
    total_parts = manifest["total_parts"]

    print(f"Manifest loaded: {manifest_path}")
    print(f"  Original file : {manifest['original_filename']}")
    print(f"  Expected size : {expected_total:,} bytes")
    print(f"  Parts to read : {total_parts}")

    # Decode chunks in order and assemble
    assembled = bytearray()

    for chunk_info in manifest["chunks"]:
        part_num = chunk_info["part"]
        filename = chunk_info["filename"]
        expected_chunk_sha256 = chunk_info["chunk_sha256"]

        filepath = os.path.join(chunk_dir, filename)
        if not os.path.isfile(filepath):
            raise FileNotFoundError(
                f"Chunk file missing (part {part_num}): {filepath}"
            )

        with open(filepath, "r", encoding="ascii") as cf:
            encoded = cf.read().strip()

        chunk_data = base64.b64decode(encoded)
        actual_chunk_sha256 = compute_sha256(chunk_data)

        if actual_chunk_sha256 != expected_chunk_sha256:
            raise ValueError(
                f"Checksum mismatch on part {part_num} ({filename})!\n"
                f"  Expected : {expected_chunk_sha256}\n"
                f"  Got      : {actual_chunk_sha256}\n"
                "The chunk file may be corrupted or incomplete."
            )

        assembled.extend(chunk_data)
        print(f"  Part {part_num:03d} OK  ({len(chunk_data):,} bytes)")

    # Verify total size
    if len(assembled) != expected_total:
        raise ValueError(
            f"Size mismatch after assembly!\n"
            f"  Expected : {expected_total:,} bytes\n"
            f"  Got      : {len(assembled):,} bytes"
        )

    # Verify whole-file checksum
    actual_file_sha256 = compute_sha256(bytes(assembled))
    if actual_file_sha256 != expected_file_sha256:
        raise ValueError(
            f"File SHA-256 mismatch after assembly!\n"
            f"  Expected : {expected_file_sha256}\n"
            f"  Got      : {actual_file_sha256}\n"
            "Data may have been corrupted or tampered with during transfer."
        )

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "wb") as out:
        out.write(assembled)

    print(f"\nReconstruction complete.")
    print(f"  Output file   : {output_path}")
    print(f"  Total bytes   : {len(assembled):,}")
    print(f"  File SHA-256  : {actual_file_sha256}  ✓")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def usage() -> None:
    print(__doc__)
    sys.exit(1)


def main() -> None:
    args = sys.argv[1:]

    if not args or args[0] not in ("split", "reconstruct"):
        usage()

    command = args[0]

    if command == "split":
        if len(args) < 2:
            print("Error: missing <file.zip> argument.")
            usage()
        zip_path = args[1]
        output_dir = args[2] if len(args) >= 3 else os.path.join(
            os.path.dirname(os.path.abspath(zip_path)),
            os.path.splitext(os.path.basename(zip_path))[0] + "_chunks",
        )
        split_zip(zip_path, output_dir)

    elif command == "reconstruct":
        if len(args) < 3:
            print("Error: missing <chunk_dir> and/or <output.zip> arguments.")
            usage()
        chunk_dir = args[1]
        output_path = args[2]
        reconstruct_zip(chunk_dir, output_path)


if __name__ == "__main__":
    main()