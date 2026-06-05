from pathlib import Path
import os
import shutil

from .styledown import extract_metadata, render_styledown_page, Metadata, IndexEntry, Breadcrumb

def get_all_relative_file_paths(directory: Path) -> list[Path]:
    return [
        path.relative_to(directory)
        for path in directory.rglob("*")
        if path.is_file()
    ]

def get_href_for_index_entry(current_directory: Path, path: Path) -> str:
    path = path.relative_to(current_directory)
    if str(path) == "index.md":
        return "."
    elif path.name == "index.md":
        return path.parent.name + "/"
    elif path.suffix == ".md":
        return path.stem
    else:
        return path.name

def get_metadata(path: Path) -> Metadata:
    if path.suffix == ".md":
        return extract_metadata(path.read_text(encoding="utf-8"))
    else:
        return Metadata(title=path.name, description="")

def get_index_entries(input_path: Path) -> list[IndexEntry]:
    current_filename = input_path.name
    current_directory = input_path.parent
    index_entries = []
    for path in current_directory.iterdir():
        if path.name == current_filename:
            continue
        if path.is_dir():
            path = path / "index.md"
        if not path.exists():
            raise Exception(f"{path} does not exist")
        metadata = get_metadata(path)
        href = get_href_for_index_entry(current_directory, path)
        index_entries.append(IndexEntry(href=href, title=metadata.title, description=metadata.description))
    return index_entries

def title_from_md(md_path: Path) -> str:
    if not md_path.exists():
        raise Exception(f"{md_path} does not exist")
    return extract_metadata(md_path.read_text(encoding="utf-8")).title

def get_breadcrumbs(root_directory: Path, relative_path: Path) -> list[Breadcrumb]:
    breadcrumbs = [Breadcrumb(title="Home", href="/")]
    current = root_directory
    parts = relative_path.parts[:-1] if relative_path.name == "index.md" else relative_path.parts
    href_parts = []
    for name in parts:
        current = current / name
        href_parts.append(name)
        if current.is_dir():
            title = title_from_md(current / "index.md")
        elif current.suffix == ".md":
            title = title_from_md(current)
        else:
            title = current.name
        href = "/" + "/".join(href_parts) + "/"
        breadcrumbs.append(Breadcrumb(title=title, href=href))
    return breadcrumbs

def build_md_file(input_directory: Path, output_directory: Path, relative_path: Path) -> None:
    input_path = input_directory / relative_path
    output_path = output_directory / relative_path.with_suffix(".html")
    input_text = input_path.read_text(encoding="utf-8")
    index_entries = get_index_entries(input_path) if ":index:" in input_text else []
    breadcrumbs = get_breadcrumbs(input_directory, relative_path)
    html = render_styledown_page(input_text, index_entries, breadcrumbs)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")

def build_site(input_directory: Path, output_directory: Path) -> None:
    print(f" [+] Removing and creating {output_directory}")
    shutil.rmtree(output_directory, ignore_errors=True)
    output_directory.mkdir(parents=True, exist_ok=True)
    relative_paths = get_all_relative_file_paths(input_directory)
    print(f" [+] Detected {len(relative_paths)} files to copy or render")
    for relative_path in relative_paths:
        if relative_path.suffix == ".md":
            print(f" [+] Rendering {relative_path}")
            try:
                build_md_file(input_directory, output_directory, relative_path)
            except Exception as e:
                raise Exception(f"Error processing {relative_path}: {e}")
        else:
            print(f" [+] Copying {relative_path}")
            input_path = input_directory / relative_path
            output_path = output_directory / relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(input_path, output_path)

def build_sites(input_directory: Path, output_directory: Path) -> None:
    print(f" [+] Removing and creating {output_directory}")
    shutil.rmtree(output_directory, ignore_errors=True)
    output_directory.mkdir(parents=True, exist_ok=True)
    domain_directories = [path for path in input_directory.iterdir() if path.is_dir()]
    for domain_directory in domain_directories:
        if domain_directory.is_symlink():
            print(f" [+] Creatinig symlink for {domain_directory.name}")
            new_symlink = output_directory / domain_directory.name
            new_symlink.parent.mkdir(parents=True, exist_ok=True)
            new_symlink.symlink_to(os.readlink(domain_directory), target_is_directory=True)
        else:
            build_site(domain_directory, output_directory / domain_directory.name)
