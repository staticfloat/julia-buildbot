# Helper function to generate the necessary julia invocation to get metadata
# about this build such as major/minor versions
@util.renderer
def make_julia_version_command(props_obj):
    command = [
        "usr/bin/julia",
        "-e",
        "println(\"$(VERSION.major).$(VERSION.minor).$(VERSION.patch)\\n$(Base.GIT_VERSION_INFO.commit[1:10])\")"
    ]

    if is_windows(props_obj):
        command[0] = 'usr\\bin\\julia.exe'
    return command

# Parse out the full julia version generated by make_julia_version_command's command
def parse_julia_version(return_code, stdout, stderr):
    lines = stdout.split('\n')
    return {
        "majmin": lines[0][:lines[0].rfind('.')],
        "version": lines[0].strip(),
        "shortcommit": lines[1].strip(),
    }

def parse_git_log(return_code, stdout, stderr):
    lines = stdout.split('\n')
    return {
        "commitmessage": lines[0],
        "commitname": lines[1],
        "commitemail": lines[2],
        "authorname": lines[3],
        "authoremail": lines[4],
    }

def gen_local_filename(props_obj, ext=".{os_pkg_ext}"):
    props = props_obj_to_dict(props_obj)

    # Get the output of the `make print-JULIA_BINARYDIST_FILENAME` step
    artifact = "{artifact_filename}".format(**props).strip()
    return artifact[26:] + ext.format(**props)

# Map from property to upload OS name on the JuliaLang S3 bucket
def get_upload_os_name(props):
    if is_windows(props):
        return "winnt"
    elif is_mac(props):
        return "mac"
    elif is_freebsd(props):
        return "freebsd"
    elif is_musl(props):
        return "musl"
    else:
        return "linux"

def gen_upload_filename(props_obj, ext=".{os_pkg_ext}"):
    props = props_obj_to_dict(props_obj)
    # We don't like "winnt" at the end of files, we use just "win" instead.
    props["os_name_file"] = props["os_name"]
    if props["os_name_file"] == "winnt":
        props["os_name_file"] = "win"
    filename_format = "julia-{shortcommit}-{os_name_file}{bits}%s"%(ext)
    return filename_format.format(**props)

def gen_upload_path(props_obj, namespace="bin", store_majmin=True, latest=False):
    # First, pull information out of props_obj
    up_arch = props_obj.getProperty("up_arch")
    majmin = props_obj.getProperty("majmin")
    upload_filename = props_obj.getProperty("upload_filename")
    assert_build = props_obj.getProperty("assert_build")

    # If we're asking for the latest information,
    if latest and upload_filename[:6] == "julia-":
        split_name = upload_filename.split("-")
        upload_filename = "julia-latest-%s"%(split_name[2])
    os = get_upload_os_name(props_obj)

    # If we're running an assert build, put it into an "assert" bucket:
    if assert_build:
        namespace = "assert_" + namespace

    # If we're running on the buildog or some other branch, prepend all our namespaces:
    if BUILDBOT_BRANCH != "master":
        namespace = BUILDBOT_BRANCH + "_" + namespace

    # If we have a namespace, add that on to our URL first
    url = "julialangnightlies/" + namespace + "/"

    # Next up, OS and Arch.
    url += os + "/" + up_arch + "/"

    # If we're asking for latest, don't go into majmin
    if store_majmin:
        url += majmin + "/"
    url += upload_filename

    return url

def gen_download_url(props_obj, namespace="bin", store_majmin=True, latest=False):
    base = 'https://s3.amazonaws.com'
    return '%s/%s'%(base, gen_upload_path(props_obj, namespace=namespace, store_majmin=store_majmin, latest=latest))


# This is a weird buildbot hack where we really want to parse the output of our
# make command, but we also need access to our properties, which we can't get
# from within an `extract_fn`.  So we save the output from a previous
# SetPropertyFromCommand invocation, then invoke a new command through this
# @util.renderer nonsense.  This function is supposed to return a new command
# to be executed, but it has full access to all our properties, so we do all our
# artifact filename parsing/munging here, then return ["true"] as the step
# to be executed.
@util.renderer
def munge_artifact_filename(props_obj):
    # Generate our local and upload filenames
    local_filename = gen_local_filename(props_obj)
    local_zip_name = gen_local_filename(props_obj, ".zip")
    local_tarball_name = gen_local_filename(props_obj, ".tar.gz")
    upload_filename = gen_upload_filename(props_obj)
    upload_zip_name = gen_upload_filename(props_obj, ".zip")
    upload_tarball_name = gen_upload_filename(props_obj, ".tar.gz")

    props_obj.setProperty("local_tarball_name", local_tarball_name, "munge_artifact_filename")
    props_obj.setProperty("local_zip_name", local_zip_name, "munge_artifact_filename")
    props_obj.setProperty("local_filename", local_filename, "munge_artifact_filename")
    props_obj.setProperty("upload_filename", upload_filename, "munge_artifact_filename")
    props_obj.setProperty("upload_zip_name", upload_zip_name, "munge_artifact_filename")
    props_obj.setProperty("upload_tarball_name", upload_tarball_name, "munge_artifact_filename")
    return ["true"]

@util.renderer
def render_upload_command(props_obj):
    upload_path = gen_upload_path(props_obj, namespace="pretesting")
    upload_filename = props_obj.getProperty("upload_filename")
    upload_tarball_name = props_obj.getProperty("upload_tarball_name")
    upload_tarball_path = upload_path.replace(upload_filename, upload_tarball_name)
    upload_zip_name = props_obj.getProperty("upload_zip_name")
    upload_zip_path = upload_path.replace(upload_filename, upload_zip_name)
    zip_upload_cmd = ""
    if is_windows(props_obj):
        zip_upload_cmd = "aws s3 cp --acl public-read /tmp/julia_package/%s s3://%s ;"%(upload_zip_name, upload_zip_path)
    return ["sh", "-c",
        "[ '%s' != '%s' ] && aws s3 cp --acl public-read /tmp/julia_package/%s s3://%s ;"%(upload_filename, upload_tarball_name, upload_tarball_name, upload_tarball_path) +
        zip_upload_cmd +
        "aws s3 cp --acl public-read /tmp/julia_package/%s.asc s3://%s.asc ; "%(upload_filename, upload_path) +
        "aws s3 cp --acl public-read /tmp/julia_package/%s s3://%s ;"%(upload_filename, upload_path)
    ]

@util.renderer
def render_srcdist_upload_command(props_obj):
    JULIA_VERSION = props_obj.getProperty("JULIA_VERSION")
    JULIA_COMMIT = props_obj.getProperty("JULIA_COMMIT")
    majmin = JULIA_VERSION[0:3]

    # First, the most specific names.
    light_filename  = "julia-" + JULIA_VERSION + "_" + JULIA_COMMIT + ".tar.gz"
    full_filename   = "julia-" + JULIA_VERSION + "_" + JULIA_COMMIT + "-full.tar.gz"
    fullbb_filename = "julia-" + JULIA_VERSION + "_" + JULIA_COMMIT + "-full+bb.tar.gz"
    light_upload_path  = "julialangnightlies/src/" + majmin + "/julia-" + majmin + "-" + JULIA_COMMIT + ".tar.gz"
    full_upload_path   = "julialangnightlies/src/" + majmin + "/julia-" + majmin + "-" + JULIA_COMMIT + "-full.tar.gz"
    fullbb_upload_path = "julialangnightlies/src/" + majmin + "/julia-" + majmin + "-" + JULIA_COMMIT + "-full+bb.tar.gz"

    # Next, the majmin-latest paths
    light_majmin_latest_path  = "julialangnightlies/src/" + majmin + "/julia-latest.tar.gz"
    full_majmin_latest_path   = "julialangnightlies/src/" + majmin + "/julia-latest-full.tar.gz"
    fullbb_majmin_latest_path = "julialangnightlies/src/" + majmin + "/julia-latest-full+bb.tar.gz"

    # Finally, the latest paths
    light_latest_path  = "julialangnightlies/src/julia-latest.tar.gz"
    full_latest_path   = "julialangnightlies/src/julia-latest-full.tar.gz"
    fullbb_latest_path = "julialangnightlies/src/julia-latest-full+bb.tar.gz"

    cmds = ""
    # First, build up the commands to upload the majmin-specific names
    for (filename, path) in ((light_filename, light_upload_path),
                             (full_filename, full_upload_path),
                             (fullbb_filename, fullbb_upload_path)):
        cmds += "aws s3 cp --acl public-read /tmp/julia_package/%s.asc s3://%s.asc ; "%(filename, path)
        cmds += "aws s3 cp --acl public-read /tmp/julia_package/%s s3://%s ; "%(filename, path)
    
    # Next, We'll copy these to the majmin-latest and latest paths
    for (path, majmin_latest_path, latest_path) in ((light_upload_path, light_majmin_latest_path, light_latest_path),
                                                    (full_upload_path, full_majmin_latest_path, full_latest_path),
                                                    (fullbb_upload_path, fullbb_majmin_latest_path, fullbb_latest_path)):
        cmds += "aws s3 cp --acl public-read s3://%s.asc s3://%s.asc ; "%(path, majmin_latest_path)
        cmds += "aws s3 cp --acl public-read s3://%s s3://%s ; "%(path, majmin_latest_path)
        cmds += "aws s3 cp --acl public-read s3://%s.asc s3://%s.asc ; "%(path, latest_path)
        cmds += "aws s3 cp --acl public-read s3://%s s3://%s ; "%(path, latest_path)

    # Chop off the final `"; "` before passing to `sh`:
    return ["sh", "-c", cmds[:-2]]

@util.renderer
def render_promotion_command(props_obj):
    src_path = gen_upload_path(props_obj, namespace="pretesting")
    dst_path = gen_upload_path(props_obj)
    return ["sh", "-c",
        "aws s3 cp --acl public-read s3://%s.asc s3://%s.asc ; "%(src_path, dst_path) +
        "aws s3 cp --acl public-read s3://%s s3://%s "%(src_path, dst_path),
    ]

@util.renderer
def render_majmin_promotion_command(props_obj):
    src_path = gen_upload_path(props_obj, namespace="pretesting")
    dst_majmin_path = gen_upload_path(props_obj, latest=True)
    return ["sh", "-c",
        "aws s3 cp --acl public-read s3://%s.asc s3://%s.asc ; "%(src_path, dst_majmin_path) +
        "aws s3 cp --acl public-read s3://%s s3://%s"%(src_path, dst_majmin_path),
    ]

@util.renderer
def render_latest_promotion_command(props_obj):
    src_path = gen_upload_path(props_obj, namespace="pretesting")
    dst_path = gen_upload_path(props_obj, store_majmin=False, latest=True)
    return ["sh", "-c",
        "aws s3 cp --acl public-read s3://%s.asc s3://%s.asc ; "%(src_path, dst_path) +
        "aws s3 cp --acl public-read s3://%s s3://%s"%(src_path, dst_path),
    ]

@util.renderer
def render_download_url(props_obj):
    return gen_download_url(props_obj)

@util.renderer
def render_pretesting_download_url(props_obj):
    return gen_download_url(props_obj, namespace="pretesting")

def build_download_julia_cmd(props_obj):
    download_url = props_obj.getProperty("download_url")

    # Build commands to download/install julia
    cmd = ""
    if is_mac(props_obj):
        # Download the .dmg
        cmd += "curl -L '%s' -o julia-installer.dmg && "%(download_url)
        # Mount it
        cmd += "hdiutil mount julia-installer.dmg -mountpoint ./dmg_mount && "
        # copy its `julia` folder contents here.
        cmd += "cp -Ra ./dmg_mount/Julia-*.app/Contents/Resources/julia/* . && "
        # Unmount any and all Julia disk images
        cmd += "hdiutil detach dmg_mount && "
        # Delete the .dmg
        cmd += "rm -f julia-installer.dmg"
    elif is_windows(props_obj):
        # Download the .exe
        cmd += "curl -L '%s' -o julia-installer.exe && "%(download_url)
        # Make it executable
        cmd += "chmod +x julia-installer.exe && "
        # Extract it into the current directory.  Note that for 1.4, we switched to a different
        # compression scheme, meaning we must polymorph here.
        if props_obj.getProperty("majmin") in ("1.0", "1.1", "1.2", "1.3"):
            cmd += "./julia-installer.exe /S /D=$(cygpath -w $(pwd)) && "
        else:
            cmd += "./julia-installer.exe /VERYSILENT /DIR=$(cygpath -w $(pwd)) && "
        # Remove the .exe
        cmd += "rm -f julia-installer.exe"
    else:
        # Oh linux.  Your simplicity always gets me
        cmd = "curl -L '%s' | tar --strip-components=1 -zxf -"%(download_url)
    return ["sh", "-c", cmd]


@util.renderer
def download_julia(props_obj, namespace="bin"):
    # If we already have an "url", use that, otherwise try to generate it:
    if not props_obj.hasProperty('download_url'):
        # Calculate upload_filename, add to properties, then get download url
        upload_filename = gen_upload_filename(props_obj)
        props_obj.setProperty("upload_filename", upload_filename, "download_julia")
        download_url = gen_download_url(props_obj)
        props_obj.setProperty("download_url", download_url, "download_julia")
    return build_download_julia_cmd(props_obj)

def download_latest_julia(props_obj):
    # Fake `gen_upload_filename()` into giving us something like
    # `julia-latest-linux64.tar.gz` instead of a true shortcommit
    props_obj.setProperty("shortcommit", "latest", "download_latest_julia")
    props_obj.setProperty(
        "upload_filename",
        gen_upload_filename(props_obj),
        "download_latest_julia",
    )
    props_obj.setProperty(
        "download_url",
        gen_download_url(props_obj, store_majmin=False, latest=True),
        "download_latest_julia",
    )
    return build_download_julia_cmd(props_obj)

@util.renderer
def render_tester_name(props_obj):
    props = props_obj_to_dict(props_obj)
    return "Julia CI (%s testing)"%(props['buildername'].replace('package_', ''))
