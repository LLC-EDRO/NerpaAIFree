"""Job-local IPC client for the sandbox's warm converter; never a network API."""
import sys
import time
from pathlib import Path
import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.document.MacroExecMode import NEVER_EXECUTE
from com.sun.star.document.UpdateDocMode import NO_UPDATE


def prop(name, value):
    item = PropertyValue()
    item.Name, item.Value = name, value
    return item


def convert(source, target):
    local = uno.getComponentContext()
    resolver = local.ServiceManager.createInstanceWithContext('com.sun.star.bridge.UnoUrlResolver', local)
    deadline = time.monotonic() + 30
    while True:
        try:
            context = resolver.resolve('uno:pipe,name=nerpa_job_renderer;urp;StarOffice.ComponentContext')
            break
        except Exception:
            if time.monotonic() >= deadline:
                raise RuntimeError('pptx_renderer_connect_failed') from None
            time.sleep(.1)
    desktop = context.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', context)
    document = desktop.loadComponentFromURL(source.as_uri(), '_blank', 0, (
        prop('Hidden', True), prop('ReadOnly', True), prop('Silent', True),
        prop('MacroExecutionMode', NEVER_EXECUTE), prop('UpdateDocMode', NO_UPDATE),
    ))
    if document is None:
        raise RuntimeError('pptx_render_failed')
    try:
        document.storeToURL(target.as_uri(), (prop('FilterName', 'impress_pdf_Export'), prop('Overwrite', True)))
    finally:
        document.close(True)


if __name__ == '__main__':
    convert(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
