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
#include "NodeVisibility.h"

#include <ILayerProperties.h>
#include <INodeLayerProperties.h>
#include <inode.h>

namespace MAXUSD_NS_DEF {
namespace NodeVisibility {

bool IsHiddenIncludingLayer(INode* node)
{
    if (!node) {
        return false;
    }
    // The node's own flag and Hide-By-Category first: cheap, and covers most hidden objects.
    if (node->IsNodeHidden()) {
        return true;
    }
    auto* nodeLayerProps = static_cast<INodeLayerProperties*>(
        node->GetInterface(NODELAYERPROPERTIES_INTERFACE));
    if (!nodeLayerProps) {
        return false;
    }
    // Layers nest, so a node on a visible layer whose PARENT layer is off is still hidden.
    // getParentLayerProperties() returns nullptr at the root, which ends the walk.
    for (ILayerProperties* layer = nodeLayerProps->getLayer(); layer != nullptr;
         layer = layer->getParentLayerProperties()) {
        if (!layer->getOn()) {
            return true;
        }
    }
    return false;
}

} // namespace NodeVisibility
} // namespace MAXUSD_NS_DEF
