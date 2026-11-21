/* SPDX-License-Identifier: MIT */
/* OpenRAC Editor: the first steps card: how to make a level, in three steps, until "Got it". */

#pragma once

#include "scene/gui/control.h"

class PanelContainer;

// OpenRAC Editor: a card in the corner of the level view: what to do first (add from Assets,
// change in Properties, Play Level). It stays until "Got it", then never comes back.
class EditorFirstSteps : public Control {
	GDCLASS(EditorFirstSteps, Control);

	PanelContainer *card = nullptr;

	void _got_it();

protected:
	void _notification(int p_what);

public:
	static bool is_wanted();

	EditorFirstSteps();
};
