//
// Copyright 2026 Miris
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.
//
#pragma once
#include <MaxUsd/MaxUSDAPI.h>

#include <pxr/usd/usd/stage.h>

#include <MaxUsd.h>

#include <string>

namespace MAXUSD_NS_DEF {
namespace PortableAssetPath {

/**
 * \brief Turn a resolved absolute texture path into one that survives leaving this machine.
 *
 * Resolving a path and making it portable are different jobs, and the exporter only did the first.
 * MAX-TEX-003 routes every bitmap through `FileResolutionManager.getFullFilePath`, which correctly
 * returns where the file actually IS on the exporting machine -- and on an arch-viz workstation
 * that is a mapped drive, so the authored value came out as
 * `Y:\denver\Visualization\...\foo.tx`. Resolution succeeded; the path was still valid on exactly
 * one computer. A measured arena carried 135 such references.
 *
 * USD expects a portable asset to name its textures RELATIVE to the layer that authors them, with
 * the files sitting beside that layer. This does that: the texture is copied next to the layer if
 * it is not already underneath it, and the returned string is layer-relative. Both material halves
 * then agree, the crate is portable on its own, and no downstream repair pass is needed -- which
 * matters because a repair authored as an override in the ROOT layer hides an unportable path in
 * the crate, so a consumer reading the crate alone still gets nothing.
 *
 * Deliberately conservative: anything it cannot do safely leaves the path exactly as it was, so a
 * failure degrades to today's behaviour rather than to a broken asset.
 *
 * \param resolvedAbsolutePath The path as returned by Max's file resolver.
 * \param stage The stage being written; its edit target names the layer to be relative to.
 * \param subDir Folder beside the layer to copy into. Created on demand.
 * \return A layer-relative path (forward slashes, `./`-prefixed), or the input unchanged.
 */
MaxUSDAPI std::string MakePortable(
    const std::string&         resolvedAbsolutePath,
    const pxr::UsdStageRefPtr& stage,
    const std::string&         subDir = "textures");

} // namespace PortableAssetPath
} // namespace MAXUSD_NS_DEF
