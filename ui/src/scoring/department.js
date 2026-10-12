/* The Database grid's per-department score columns (DI / SI / SMO) read a run's stored
   pre-department assessment. A stored score counts only while the department is still defined the
   same way; one assessed against since-changed interests is shown as "Not assessed", not reused. */
function sameDepartment(saved, current) {
  return Boolean(saved && current && saved.label === current.label &&
    JSON.stringify(saved.interests) === JSON.stringify(current.interests));
}
export function departmentScore(run, department) {
  const saved = run.department_assessments?.[department?.id];
  return saved?.status === "assessed" && sameDepartment(saved.department, department) && typeof saved.score === "number" ? saved.score : null;
}
