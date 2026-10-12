import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import ColumnDrawer from "./ColumnDrawer.jsx";
import { DEFAULT_COLS } from "./exploreColumns.jsx";

describe("ColumnDrawer", () => {
  it("restores the default columns", () => {
    const setCols = vi.fn();
    render(<ColumnDrawer open onClose={() => {}} cols={["hq"]} setCols={setCols} onSaveView={() => {}} />);
    fireEvent.click(screen.getByRole("button", { name: "Restore defaults" }));
    expect(setCols).toHaveBeenCalledWith(DEFAULT_COLS);
  });
});
