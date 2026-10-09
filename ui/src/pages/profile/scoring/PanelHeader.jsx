import React from "react";
import { IxContentHeader } from "@siemens/ix-react";

/* A score panel's header. In its popup (ScoreGrid) the dialog's own header carries the title, so
   only the subtitle stays — it is the line that says what the numbers below are. */
export default function PanelHeader({ title, subtitle, inDialog = false }) {
  if (inDialog) return subtitle ? <p className="muted dialog-sub">{subtitle}</p> : null;
  return <IxContentHeader headerTitle={title} headerSubtitle={subtitle} />;
}
