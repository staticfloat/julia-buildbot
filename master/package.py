# Add our packagers on various platforms
julia_packagers  = ["package_osx64"] + ["package_win32", "package_win64"]
julia_packagers += ["package_linux%s"%(arch) for arch in ["32", "64", "armv7l", "ppc64le", "aarch64"]]

# Also add builders for Ubuntu and Centos builders, that won't upload anything at the end
julia_packagers += ["build_ubuntu32", "build_ubuntu64", "build_centos64"]

packager_scheduler = schedulers.AnyBranchScheduler(name="Julia binary packaging", change_filter=util.ChangeFilter(project=['JuliaLang/julia','staticfloat/julia'], branch='master'), builderNames=julia_packagers, treeStableTimer=1)
c['schedulers'].append(packager_scheduler)


# Helper function to generate the necessary julia invocation to get metadata
# about this build such as major/minor versions
@util.renderer
def make_julia_version_command(props):
    command = [
        "usr/bin/julia",
        "-e",
        "println(\"$(VERSION.major).$(VERSION.minor).$(VERSION.patch)\\n$(Base.GIT_VERSION_INFO.commit[1:10])\")"
    ]

    if 'win' in props.getProperty('slavename'):
        command[0] += '.exe'
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

@util.renderer
def gen_filename(props):
    shortcommit = props.getProperty("shortcommit")
    tar_arch = props.getProperty("tar_arch")
    if is_linux(props):
        return "julia-%s-Linux-%s.tar.gz"%(shortcommit, tar_arch)
    if is_win(props):
        return "julia-%s-WINNT-%s.exe"%(shortcommit, tar_arch)
    if is_osx(props):
        return "julia-%s-Darwin-%s.dmg"%(shortcommit, tar_arch)

# Steps to build a `make binary-dist` tarball that should work on just about every linux ever
julia_package_factory = util.BuildFactory()
julia_package_factory.useProgress = True
julia_package_factory.addSteps([
    # Fetch first (allowing failure if no existing clone is present)
    steps.ShellCommand(
        name="git fetch",
        command=["git", "fetch"],
        flunkOnFailure=False
    ),

    # Clone julia
    steps.Git(
        name="Julia checkout",
        repourl=util.Property('repository', default='git://github.com/JuliaLang/julia.git'),
        mode='incremental',
        method='clean',
        submodules=True,
        clobberOnFailure=True,
        progress=True
    ),

    # Ensure gcc and cmake are installed on OSX
    steps.ShellCommand(
        name="Install necessary brew dependencies",
        command=["brew", "install", "gcc", "cmake"],
        doStepIf=is_osx,
        flunkOnFailure=False
    ),

    # make clean first
    steps.ShellCommand(
        name="make cleanall",
        command=["/bin/bash", "-c", util.Interpolate("make %(prop:flags)s cleanall")],
        env={'CFLAGS':None, 'CPPFLAGS':None}
    ),

    # Make, forcing some degree of parallelism to cut down compile times
    steps.ShellCommand(
        name="make",
        command=["/bin/bash", "-c", util.Interpolate("make -j3 %(prop:flags)s")],
        haltOnFailure = True,
        timeout=3600,
        env={'CFLAGS':None, 'CPPFLAGS':None}
    ),

    # Test this build
    steps.ShellCommand(
        name="make testall",
        command=["/bin/bash", "-c", util.Interpolate("make %(prop:flags)s testall")],
        haltOnFailure = True,
        timeout=3600,
        env={'CFLAGS':None, 'CPPFLAGS':None}
    ),

    # Make win-extras on windows
    steps.ShellCommand(
        name="make win-extras",
        command=["/bin/bash", "-c", util.Interpolate("make %(prop:flags)s win-extras")],
        haltOnFailure = True,
        doStepIf=is_windows,
        env={'CFLAGS':None, 'CPPFLAGS':None},
    ),

    # Make binary-dist to package it up
    steps.ShellCommand(
        name="make binary-dist",
        command=["/bin/bash", "-c", util.Interpolate("make %(prop:flags)s binary-dist")],
        haltOnFailure = True,
        timeout=3600,
        env={'CFLAGS':None, 'CPPFLAGS':None},
    ),

    # Set a bunch of properties that are useful down the line
    steps.SetPropertyFromCommand(
        name="Get julia version/shortcommit",
        command=make_julia_version_command,
        extract_fn=parse_julia_version,
        want_stderr=False
    ),
    steps.SetPropertyFromCommand(
        name="Get commitmessage",
        command=["git", "log", "-1", "--pretty=format:%s%n%cN%n%cE%n%aN%n%aE"],
        extract_fn=parse_git_log,
        want_stderr=False
    ),

    # Transfer the result to the buildmaster for uploading to AWS
    steps.MasterShellCommand(
        name="mkdir julia_package",
        command=["mkdir", "-p", "/tmp/julia_package"]
    ),
    steps.FileUpload(
        workersrc=util.Interpolate("julia-%(prop:shortcommit)s-Linux-%(prop:tar_arch)s.tar.gz"),
        masterdest=util.Interpolate("/tmp/julia_package/julia-%(prop:shortcommit)s-Linux-%(prop:tar_arch)s.tar.gz")
    ),

    # Upload it to AWS and cleanup the master!
    steps.MasterShellCommand(
        name="Upload to AWS",
        command=["/bin/bash", "-c", util.Interpolate("~/bin/try_thrice ~/bin/aws put --fail --public julianightlies/bin/linux/%(prop:up_arch)s/%(prop:majmin)s/julia-%(prop:version)s-%(prop:shortcommit)s-linux%(prop:bits)s.tar.gz /tmp/julia_package/julia-%(prop:shortcommit)s-Linux-%(prop:tar_arch)s.tar.gz")],
        doStepIf=should_upload,
        haltOnFailure=True
    ),
    steps.MasterShellCommand(
        name="Upload to AWS (latest)",
        command=["/bin/bash", "-c", util.Interpolate("~/bin/try_thrice ~/bin/aws put --fail --public julianightlies/bin/linux/%(prop:up_arch)s/julia-latest-linux%(prop:bits)s.tar.gz /tmp/julia_package/julia-%(prop:shortcommit)s-Linux-%(prop:tar_arch)s.tar.gz")],
        doStepIf=should_upload_latest,
        haltOnFailure=True
    ),

    steps.MasterShellCommand(
        name="Cleanup Master",
        command=["rm", "-f", util.Interpolate("/tmp/julia_package/julia-%(prop:shortcommit)s-Linux-%(prop:tar_arch)s.tar.gz")],
        doStepIf=should_upload
    ),

    # Trigger a download of this file onto another slave for coverage purposes
    steps.Trigger(schedulerNames=["Julia Coverage Testing"],
        set_properties={
            'url': util.Interpolate('https://s3.amazonaws.com/julianightlies/bin/linux/%(prop:up_arch)s/%(prop:majmin)s/julia-%(prop:version)s-%(prop:shortcommit)s-linux%(prop:bits)s.tar.gz'),
            'commitmessage': util.Property('commitmessage'),
            'commitname': util.Property('commitname'),
            'commitemail': util.Property('commitemail'),
            'authorname': util.Property('authorname'),
            'authoremail': util.Property('authoremail'),
            'shortcommit': util.Property('shortcommit'),
        },
        waitForFinish=False,
        doStepIf=should_run_coverage
    )
])


# Map each builder to each packager
mapping = {
    "package_osx64": "osx10_10-x64",
    "package_win32": "win6_2-x86",
    "package_win64": "win6_2-x64",
    "package_linux32": "centos5_11-x86",
    "package_linux64": "centos5_11-x64",
    "package_linuxarmv7l": "debian7_11-armv7l",
    "package_linuxppc64le": "centos7_2-ppc64le",
    "package_linuxaarch64": "centos7_2-aarch64",

    # These builders don't get uploaded
    "build_ubuntu32": "ubuntu14_04-x86",
    "build_ubuntu64": "ubuntu14_04-x64",
    "build_centos64": "centos7_1-x64",
}
for packager, slave in mapping.iteritems():
    c['builders'].append(util.BuilderConfig(
        name=packager,
        workernames=[slave],
        tags=["Packaging"],
        factory=julia_package_factory
    ))


# Add a scheduler for building release candidates/triggering builds manually
force_build_scheduler = schedulers.ForceScheduler(
    name="force_julia_package",
    label="Force Julia build/packaging",
    builderNames=julia_packagers,
    reason=util.FixedParameter(name="reason", default=""),
    codebases=[
        util.CodebaseParameter(
            "",
            name="",
            branch=util.FixedParameter(name="branch", default=""),
            repository=util.FixedParameter(name="repository", default=""),
            project=util.FixedParameter(name="project", default="Packaging"),
        )
    ],
    properties=[]
)
c['schedulers'].append(force_build_scheduler)