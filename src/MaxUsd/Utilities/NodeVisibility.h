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

#include <MaxUsd.h>

class INode;

namespace MAXUSD_NS_DEF {
namespace NodeVisibility {

/**
 * \brief Is this node hidden in the viewport by ANY means, including a switched-off layer?
 *
 * `INode::IsNodeHidden()` is not enough, and the SDK says so plainly: inode.h documents it as
 * accounting for "the node hidden attribute and the 'Hide By Category' flags". Layers are absent
 * from that list. So an object sitting on a layer the artist switched off, but not itself hidden,
 * reports visible -- and the exporter authored no `visibility` for it.
 *
 * Measured on the generic arena, where every `Z_`-prefixed layer is hidden deliberately: 11 prims
 * came out visible while their siblings on the same layers came out invisible (interior 31 hidden /
 * 4 leaked, exterior 51 / 7). The ones that worked were individually hidden; the leaks were hidden
 * only by their layer. Same prim type, same hierarchy depth, so nothing about the USD structure
 * explained it -- only the source state did.
 *
 * Walks the layer's parent chain, because Max layers nest and a child layer of a hidden parent is
 * hidden too. `getOn()` is the layer's own visibility toggle: off means everything on it is hidden.
 */
MaxUSDAPI bool IsHiddenIncludingLayer(INode* node);

} // namespace NodeVisibility
} // namespace MAXUSD_NS_DEF
