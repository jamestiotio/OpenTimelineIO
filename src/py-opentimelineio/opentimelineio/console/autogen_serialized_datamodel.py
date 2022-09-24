#!/usr/bin/env python
#
# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the OpenTimelineIO project


"""Generates documentation of the serialized data model for OpenTimelineIO."""

import argparse
import inspect
import json
import tempfile
import sys

try:
    # python2
    import StringIO as io
except ImportError:
    # python3
    import io

import opentimelineio as otio


DOCUMENT_HEADER = """# Serialized Data Documentation

This documents all the OpenTimelineIO classes that serialize to and from JSON,
omitting SchemaDef plugins. This document is automatically generated by running:

`src/py-opentimelineio/opentimelineio/console/autogen_serialized_datamodel.py`

or by running:

`make doc-model`

It is part of the unit tests suite and should be updated whenever the schema
changes.  If it needs to be updated and this file regenerated, run:

`make doc-model-update`

# Class Documentation

"""

FIELDS_ONLY_HEADER = """# Serialized Data (Fields Only)

This document is a list of all the OpenTimelineIO classes that serialize to and
from JSON, omitting plugins classes and docstrings.

This document is automatically generated by running:

`src/py-opentimelineio/opentimelineio/console/autogen_serialized_datamodel.py`

or by running:

`make doc-model`

It is part of the unit tests suite and should be updated whenever the schema
changes.  If it needs to be updated and this file regenerated, run:

`make doc-model-update`


# Classes

"""

CLASS_HEADER_WITH_DOCS = """
### {classname}

*full module path*: `{modpath}`

*documentation*:

```
{docstring}
```

parameters:
"""

CLASS_HEADER_ONLY_FIELDS = """
### {classname}

parameters:
"""

MODULE_HEADER = """
## Module: {modname}
"""

PROP_HEADER = """- *{propkey}*: {prophelp}
"""

# @TODO: having type information here would be awesome
PROP_HEADER_NO_HELP = """- *{propkey}*
"""

# three ways to try and get the property + docstring
PROP_FETCHERS = (
    lambda cl, k: inspect.getdoc(getattr(cl, k)),
    lambda cl, k: inspect.getdoc(getattr(cl, "_" + k)),
    lambda cl, k: inspect.getdoc(getattr(cl(), k)) and "" or "",
)


def _parsed_args():
    """ parse commandline arguments with argparse """

    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "-d",
        "--dryrun",
        action="store_true",
        default=False,
        help="Dryrun mode - print out instead of perform actions"
    )
    group.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="Update the baseline with the current version"
    )

    return parser.parse_args()


# things to skip.  These are typically internal/private classes that don't need
# to be considered "Public Schema".
SKIP_CLASSES = [
    otio.core.SerializableObject,
    otio._otio.UnknownSchema,
    otio._otio.TestObject,
]
SKIP_KEYS = [
    "OTIO_SCHEMA",  # not data, just for the backing format
    "children",     # children are stored by container objects, implicitly
]
SKIP_MODULES = [
    "opentimelineio.schemadef",  # because these are plugins
    "opentimelineio._otio",      # C++ .so, but customers should use the full
    "opentimelineio._opentime",  # python wrapped modules (otio.schema etc)
]


def _generate_model_for_module(mod, classes, modules):
    modules.add(mod)

    # fetch the classes from this module
    serializable_classes = [
        thing for thing in mod.__dict__.values()
        if (
            inspect.isclass(thing)
            and thing not in classes
            and issubclass(thing, otio.core.SerializableObject)
            or thing in (
                otio.opentime.RationalTime,
                otio.opentime.TimeRange,
                otio.opentime.TimeTransform,
            )
        )
    ]

    # serialize/deserialize the classes to capture their serialized parameters
    model = {}
    for cl in serializable_classes:
        if cl in SKIP_CLASSES:
            continue

        model[cl] = {}
        field_dict = json.loads(otio.adapters.otio_json.write_to_string(cl()))
        for k in field_dict.keys():
            if k in SKIP_KEYS:
                continue

            for fetcher in PROP_FETCHERS:
                try:
                    # Serialized fields are almost always properties, but skip
                    # over those that are not
                    model[cl][k] = fetcher(cl, k) if isinstance(
                        getattr(cl, k), property) else ""
                    break
                except AttributeError:
                    pass
            else:
                sys.stderr.write("ERROR: could not fetch property: {}".format(k))

        # Stashing the OTIO_SCHEMA back into the dictionary since the
        # documentation uses this information in its header.
        model[cl]["OTIO_SCHEMA"] = field_dict["OTIO_SCHEMA"]

    classes.update(model)

    # find new modules to recurse into
    new_mods = sorted(
        (
            thing for thing in mod.__dict__.values()
            if (
                inspect.ismodule(thing)
                and thing not in modules
                and all(not thing.__name__.startswith(t) for t in SKIP_MODULES)
            )
        ),
        key=lambda mod: str(mod)
    )

    # recurse into the new modules and update the classes and modules values
    [_generate_model_for_module(m, classes, modules) for m in new_mods]


def _generate_model():
    classes = {}
    modules = set()
    _generate_model_for_module(otio, classes, modules)
    return classes


def _search_mod_recursively(cl, mod_to_search, already_searched):
    if cl in mod_to_search.__dict__.values():
        return mod_to_search.__name__

    child_modules = (
        m
        for m in mod_to_search.__dict__.values()
        if inspect.ismodule(m)
    )

    for submod in child_modules:
        if submod in already_searched:
            continue
        already_searched.add(submod)
        result = _search_mod_recursively(cl, submod, already_searched)
        if result is not None:
            return result

    return None


def _remap_to_python_modules(cl):
    """Find the module containing the python wrapped class, rather than the base
    C++ _otio modules.
    """

    # where the python wrapped classes live
    SEARCH_MODULES = [
        otio.schema,
        otio.opentime,
        otio.core,
    ]

    # the C++ modules
    IGNORE_MODS = set(
        [
            otio._otio,
            otio._opentime
        ]
    )

    for mod in SEARCH_MODULES:
        result = _search_mod_recursively(cl, mod, IGNORE_MODS)
        if result is not None:
            return result

    return inspect.getmodule(cl).__name__


def _write_documentation(model):
    md_with_helpstrings = io.StringIO()
    md_only_fields = io.StringIO()

    md_with_helpstrings.write(DOCUMENT_HEADER)
    md_only_fields.write(FIELDS_ONLY_HEADER)

    modules = {}
    for cl in model:
        modobj = cl.__module__

        if modobj in ['opentimelineio._opentime', 'opentimelineio._otio']:
            modobj = _remap_to_python_modules(cl)

        modules.setdefault(modobj, []).append(cl)

    CURRENT_MODULE = None
    for module_list in sorted(modules):
        this_mod = ".".join(module_list.split('.')[:2])
        if this_mod != CURRENT_MODULE:
            CURRENT_MODULE = this_mod
            md_with_helpstrings.write(MODULE_HEADER.format(modname=this_mod))
            md_only_fields.write(MODULE_HEADER.format(modname=this_mod))

        # because these are classes, they need to sort on their stringified
        # names
        for cl in sorted(modules[module_list], key=lambda cl: str(cl)):
            modname = this_mod
            label = model[cl]["OTIO_SCHEMA"]
            md_with_helpstrings.write(
                CLASS_HEADER_WITH_DOCS.format(
                    classname=label,
                    modpath=modname + "." + cl.__name__,
                    docstring=cl.__doc__
                )
            )
            md_only_fields.write(
                CLASS_HEADER_ONLY_FIELDS.format(
                    classname=label,
                )
            )

            for key, helpstr in sorted(model[cl].items()):
                if key in SKIP_KEYS:
                    continue
                md_with_helpstrings.write(
                    PROP_HEADER.format(propkey=key, prophelp=helpstr)
                )
                md_only_fields.write(
                    PROP_HEADER_NO_HELP.format(propkey=key)
                )

    return md_with_helpstrings.getvalue(), md_only_fields.getvalue()


def main():
    """  main entry point  """
    args = _parsed_args()
    with_docs, without_docs = generate_and_write_documentation()

    # print it out somewhere
    if args.dryrun:
        print(with_docs)
        return

    output = args.output
    if not output:
        output = tempfile.NamedTemporaryFile(
            'w',
            suffix="otio_serialized_schema.md",
            delete=False
        ).name

    with open(output, 'w') as fo:
        fo.write(with_docs)

    # write version without docstrings
    prefix, suffix = output.rsplit('.', 1)
    output_only_fields = prefix + "-only-fields." + suffix

    with open(output_only_fields, 'w') as fo:
        fo.write(without_docs)

    print("wrote documentation to {} and {}".format(output, output_only_fields))


def generate_and_write_documentation():
    model = _generate_model()
    return _write_documentation(model)


if __name__ == '__main__':
    main()
