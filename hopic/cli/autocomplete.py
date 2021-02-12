# Copyright (c) 2018 - 2021 TomTom N.V. (https://tomtom.com)
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
from pathlib import Path

from .utils import (
    determine_config_file_name,
)

from ..config_reader import (
        expand_vars,
        read as read_config,
    )


def _option_from_args(args, option):
    try:
        return args[args.index(option) + 1]
    except Exception:
        for arg in args:
            if arg.startswith(option + '='):
                return arg[len(option + '='):]


def _config_from_args(args):
    workspace = _option_from_args(args, "--workspace")
    if workspace is not None:
        workspace = Path(workspace)
    config = _option_from_args(args, "--config")
    if config is not None:
        config = Path(expand_vars(os.environ, config)).expanduser()
        if workspace is None:
            workspace = config.parent
    else:
        if workspace is None:
            workspace = Path.cwd()
        config = determine_config_file_name(None, workspace=workspace)
    return read_config(config, {'WORKSPACE': str(workspace)})


def phase_from_config(ctx, args, incomplete):
    try:
        cfg = _config_from_args(args)
        for phase in cfg['phases']:
            if incomplete in phase:
                yield phase
    except Exception:
        pass


def variant_from_config(ctx, args, incomplete):
    try:
        cfg = _config_from_args(args)
        phase = _option_from_args(args, '--phase')

        seen_variants = set()
        for phasename, curphase in cfg['phases'].items():
            if phase is not None and phasename != phase:
                continue
            for variant in curphase:
                if variant in seen_variants:
                    continue
                seen_variants.add(variant)
                yield variant
    except Exception:
        pass


def modality_from_config(ctx, args, incomplete):
    try:
        cfg = _config_from_args(args)
        for modality in cfg['modality-source-preparation']:
            if incomplete in modality:
                yield modality
    except Exception:
        pass


def click_log_verbosity(ctx, args, incomplete):
    for level in (
                'DEBUG',
                'INFO',
                'WARNING',
                'ERROR',
                'CRITICAL',
            ):
        if incomplete in level:
            yield level
