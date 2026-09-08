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
#include "PortableAssetPath.h"

#include <pxr/base/tf/diagnostic.h>

#include <filesystem>
#include <system_error>

// TF_WARN expands to pxr-namespaced helpers (TfCallContext, Tf_PostWarningHelper), and this file
// lives in MAXUSD_NS_DEF rather than the pxr namespace, so the directive is required.
PXR_NAMESPACE_USING_DIRECTIVE

namespace fs = std::filesystem;

namespace MAXUSD_NS_DEF {
namespace PortableAssetPath {

namespace {

// The directory the authored path will be interpreted relative to. Empty when there is nothing to
// be relative to, which is the anonymous-layer and in-memory-stage case.
fs::path _LayerDirectory(const pxr::UsdStageRefPtr& stage)
{
    if (!stage) {
        return {};
    }
    const auto layer = stage->GetEditTarget().GetLayer();
    if (!layer || layer->IsAnonymous()) {
        return {};
    }
    // Prefer the real path: an identifier can carry an asset-resolver prefix that is not a
    // filesystem location at all.
    std::string id = layer->GetRealPath();
    if (id.empty()) {
        id = layer->GetIdentifier();
    }
    if (id.empty()) {
        return {};
    }
    std::error_code ec;
    fs::path        p(id);
    if (!p.has_parent_path()) {
        return {};
    }
    p = p.parent_path();
    return fs::exists(p, ec) ? p : fs::path {};
}

std::string _ToRelativeString(const fs::path& rel)
{
    std::string s = rel.generic_string();
    if (s.empty()) {
        return s;
    }
    // USD reads a leading `./` as explicitly layer-relative. Without it a bare `textures/x.png`
    // is still relative, but the intent is easier to misread, and several of our own tools key
    // off the prefix.
    if (s.rfind("./", 0) != 0 && s.rfind("../", 0) != 0) {
        s = "./" + s;
    }
    return s;
}

} // namespace

std::string MakePortable(
    const std::string&         resolvedAbsolutePath,
    const pxr::UsdStageRefPtr& stage,
    const std::string&         subDir)
{
    if (resolvedAbsolutePath.empty()) {
        return resolvedAbsolutePath;
    }

    std::error_code ec;
    const fs::path  src(resolvedAbsolutePath);

    // Only an existing file can be relocated or reasoned about. A dangling path is left alone so
    // the authored value still records what the scene asked for.
    if (!fs::is_regular_file(src, ec)) {
        return resolvedAbsolutePath;
    }

    const fs::path layerDir = _LayerDirectory(stage);
    if (layerDir.empty()) {
        return resolvedAbsolutePath;
    }

    const fs::path srcCanon = fs::weakly_canonical(src, ec);
    const fs::path dirCanon = fs::weakly_canonical(layerDir, ec);
    if (ec) {
        return resolvedAbsolutePath;
    }

    // Already inside the layer's directory: just express it relatively, no copy.
    const fs::path already = fs::relative(srcCanon, dirCanon, ec);
    if (!ec && !already.empty() && already.native().rfind(fs::path("..").native(), 0) != 0) {
        return _ToRelativeString(already);
    }

    // Outside: copy it beside the layer.
    const fs::path destDir = dirCanon / subDir;
    fs::create_directories(destDir, ec);
    if (ec) {
        TF_WARN(
            "Could not create texture directory '%s'; leaving '%s' as an absolute path.",
            destDir.string().c_str(),
            resolvedAbsolutePath.c_str());
        return resolvedAbsolutePath;
    }

    const fs::path dest = destDir / srcCanon.filename();

    // Skip the copy when the same file is already there. Compared by size rather than content:
    // these are multi-megabyte textures and an arena references hundreds of them, so hashing every
    // one would dominate export time. A same-name, same-size collision between genuinely different
    // textures is possible, so it is reported rather than passed over in silence.
    bool copyNeeded = true;
    if (fs::exists(dest, ec)) {
        const auto destSize = fs::file_size(dest, ec);
        const auto srcSize = fs::file_size(srcCanon, ec);
        if (!ec && destSize == srcSize) {
            copyNeeded = false;
        } else if (!ec) {
            TF_WARN(
                "Texture '%s' already exists beside the layer with a different size (%llu vs "
                "%llu); overwriting with the one this material references.",
                dest.string().c_str(),
                static_cast<unsigned long long>(destSize),
                static_cast<unsigned long long>(srcSize));
        }
    }

    if (copyNeeded) {
        fs::copy_file(srcCanon, dest, fs::copy_options::overwrite_existing, ec);
        if (ec) {
            TF_WARN(
                "Could not copy texture '%s' beside the layer (%s); leaving it as an absolute "
                "path.",
                resolvedAbsolutePath.c_str(),
                ec.message().c_str());
            return resolvedAbsolutePath;
        }
    }

    const fs::path rel = fs::relative(dest, dirCanon, ec);
    if (ec || rel.empty()) {
        return resolvedAbsolutePath;
    }
    return _ToRelativeString(rel);
}

} // namespace PortableAssetPath
} // namespace MAXUSD_NS_DEF
