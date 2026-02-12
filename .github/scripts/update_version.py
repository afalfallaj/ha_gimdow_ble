import json
import re
import sys
import argparse
from pathlib import Path

def get_current_version(manifest_path):
    with open(manifest_path, 'r') as f:
        data = json.load(f)
    return data.get('version', '0.0.0')

def parse_version(version_str):
    # Regex for x.y.z or x.y.zbN
    # Groups: 1=major, 2=minor, 3=patch, 4=beta_num (optional)
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:b(\d+))?$", version_str)
    if not match:
        raise ValueError(f"Invalid version format: {version_str}")
    
    major, minor, patch, beta = match.groups()
    return int(major), int(minor), int(patch), int(beta) if beta is not None else None

def bump_version(current_version, branch):
    major, minor, patch, beta = parse_version(current_version)
    
    if branch == 'dev':
        if beta is None:
            # Stable -> Beta (bump patch + b0)
            # e.g. 1.4.0 -> 1.4.1b0
            patch += 1
            beta = 0
        else:
            # Beta -> Beta (increment beta)
            # e.g. 1.4.1b0 -> 1.4.1b1
            beta += 1
            
        new_version = f"{major}.{minor}.{patch}b{beta}"
        
    elif branch == 'main':
        if beta is not None:
            # Beta -> Stable (strip beta)
            # e.g. 1.4.1b0 -> 1.4.1
            new_version = f"{major}.{minor}.{patch}"
        else:
            # Stable -> Stable (bump patch)
            # e.g. 1.4.1 -> 1.4.2
            # This is a safety fallback if manual or unexpected merge happens
            patch += 1
            new_version = f"{major}.{minor}.{patch}"
            
    else:
        print(f"Unknown branch: {branch}. Keeping current version.")
        return current_version

    return new_version

def update_manifest(manifest_path, new_version):
    with open(manifest_path, 'r') as f:
        data = json.load(f)
    
    data['version'] = new_version
    
    with open(manifest_path, 'w') as f:
        json.dump(data, f, indent=2)
        # Add newline at end of file to match standard editors
        f.write('\n')

def update_init(init_path, new_version):
    with open(init_path, 'r') as f:
        content = f.read()
    
    # Replace __version__ = "..."
    # Using regex to be safe about spacing
    new_content = re.sub(
        r'__version__\s*=\s*["\'].*?["\']',
        f'__version__ = "{new_version}"',
        content
    )
    
    with open(init_path, 'w') as f:
        f.write(new_content)

def main():
    parser = argparse.ArgumentParser(description='Bump version based on branch')
    parser.add_argument('manifest', type=str, help='Path to manifest.json')
    parser.add_argument('init', type=str, help='Path to __init__.py')
    parser.add_argument('branch', type=str, help='Current branch name')
    
    args = parser.parse_args()
    
    try:
        current_version = get_current_version(args.manifest)
        new_version = bump_version(current_version, args.branch)
        
        if new_version != current_version:
            update_manifest(args.manifest, new_version)
            update_init(args.init, new_version)
            print(new_version)
        else:
            print(f"Skipping version bump for {current_version} on {args.branch}")
            
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == "__main__":
    main()
