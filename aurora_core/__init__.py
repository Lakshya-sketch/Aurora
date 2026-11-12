"""Aurora v2 — microkernel core.

Exposes the public surface of the kernel. The transport, plugin code,
and tests import from here; nothing inside `aurora_core/` should
reach into sibling modules via relative imports that bypass this
package boundary.
"""

__version__ = "0.1.0"
__phase__ = "0"  # skeleton + contract (see implementation_plan.md)
