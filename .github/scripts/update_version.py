import json
import re
import sys
import argparse

def get_current_version(manifest_path):
    with open(manifest_path, 'r') as f:
        data = json.load(f)
    return data.get('version', '0.0.0')

def parse_version(version_str):
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:b(\d+))?$", version_str)
    if not match:
        raise ValueError(f"Invalid version format: {version_str}")
    
    major, minor, patch, beta = match.groups()
    return int(major), int(minor), int(patch), int(beta) if beta is not None else None

def bump_version(current_version, branch, commit_message=""):
    major, minor, patch, beta = parse_version(current_version)
    
    # Check for manual overrides in the commit message
    msg = commit_message.lower()
    is_major = "[major]" in msg
    is_minor = "[minor]" in msg

    if branch == 'dev':
        if beta is None:
            # Stable -> Beta
            if is_major:
                major += 1; minor = 0; patch = 0
            elif is_minor:
                minor += 1; patch = 0
            else:
                patch += 1 # Default behavior
            beta = 0
        else:
            # Beta -> Beta (Ignore major/minor tags if we are already mid-beta cycle)
            beta += 1
            
        new_version = f"{major}.{minor}.{patch}b{beta}"
        
    elif branch == 'main':
        if beta is not None:
            # Beta -> Stable (strip beta)
            new_version = f"{major}.{minor}.{patch}"
        else:
            # Stable -> Stable (Hotfix directly to main)
            if is_major:
                major += 1; minor = 0; patch = 0
            elif is_minor:
                minor += 1; patch = 0
            else:
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
        f.write('\n')

def update_init(init_path, new_version):
    with open(init_path, 'r') as f:
        content = f.read()
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
    parser.add_argument('--message', type=str, default="", help='Commit message for bump detection')
    
    args = parser.parse_args()
    
    try:
        current_version = get_current_version(args.manifest)
        new_version = bump_version(current_version, args.branch, args.message)
        
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