import React from "react";
import { IxButton, IxToggleButton } from "@siemens/ix-react";

// Short on purpose: the full band names are what each row says, and a filter reading "Strong match"
// would be one more thing on the page claiming to be a match.
const BAND_FILTERS = { strong: "Strong", good: "Good", possible: "Possible" };

export const NO_FILTER = { bands: [], kept: false };

/** The candidates a filter lets through. No band picked means every band. */
export function applyFilter(items, filter, shortlist) {
  if (!items) return items;
  return items.filter((c) => (!filter.bands.length || filter.bands.includes(c.band))
    && (!filter.kept || shortlist.includes(c.id)));
}

export const filtering = (filter) => filter.bands.length > 0 || filter.kept;

/* Narrow both lists at once, to the bands worth reading first or to what was already kept. It only
   hides rows: nothing is re-ranked and nothing is dropped from the shortlist. */
export default function ResultFilter({ filter, onChange, shown, total }) {
  const toggleBand = (band) => onChange({ ...filter, bands: filter.bands.includes(band)
    ? filter.bands.filter((b) => b !== band) : [...filter.bands, band] });
  return (
    <div className="solve-filter" role="group" aria-label="Filter the results">
      <span className="muted">Show</span>
      {Object.entries(BAND_FILTERS).map(([band, label]) => (
        // aria-label for the same reason as the facets: ix-toggle-button otherwise names its inner
        // button from the (absent) icon.
        <IxToggleButton key={band} variant="subtle-primary" pressed={filter.bands.includes(band)} aria-label={`${label} matches`}
          onClick={() => toggleBand(band)}>{label}</IxToggleButton>
      ))}
      <IxToggleButton variant="subtle-primary" pressed={filter.kept} aria-label="Shortlisted only"
        onClick={() => onChange({ ...filter, kept: !filter.kept })}>Shortlisted only</IxToggleButton>
      {filtering(filter) && <>
        <span className="muted" role="status">Showing {shown} of {total}</span>
        <IxButton variant="tertiary" onClick={() => onChange(NO_FILTER)}>Show all</IxButton>
      </>}
    </div>
  );
}
