"""One job's warm LibreOffice instance, inside the existing no-network sandbox."""
import os
import sys
import json
import subprocess
import resource
from pathlib import Path

sys.path.insert(0,'/opt/pptx')

def main():
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_FSIZE,(160*1024**2,160*1024**2))
    os.umask(0o077)
    for folder in ('home','tmp','renderer'):
        Path('/work',folder).mkdir(exist_ok=True)
    from runtime.render import font_environment, prepare_profile
    from runtime.fonts import requested_font_names
    aliases=requested_font_names(Path('/input/font-reference.pptx'))
    work=Path('/work/renderer')
    prepare_profile(work)
    # Only this job's aliases: thousands of unrelated Fontconfig match rules
    # cost memory and can affect font selection in other families.
    env=font_environment(work,aliases)
    (work/'families.json').write_text(json.dumps(aliases))
    daemon=subprocess.Popen(['/usr/bin/soffice','-env:UserInstallation='+(work/'profile').as_uri(),
        '--headless','--nologo','--nodefault','--norestore',
        '--accept=pipe,name=nerpa_job_renderer;urp;StarOffice.ServiceManager'],
        env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    print(json.dumps(dict(ready=True)),flush=True)
    # Host teardown or the outer lifetime limit ends this isolated job worker.
    return daemon.wait()

if __name__=='__main__':
    sys.exit(main())
