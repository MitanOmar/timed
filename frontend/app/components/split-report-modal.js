import { action } from "@ember/object";
import { service } from "@ember/service";
import Component from "@glimmer/component";
import { tracked } from "@glimmer/tracking";
import { task } from "ember-concurrency";
import moment from "moment";

export default class SplitReportModal extends Component {
  @tracked isModalVisible = false;
  @service notify;
  @service store;

  @tracked newTask = null;
  @tracked newComment = "";
  @tracked newDuration = moment.duration();

  @tracked oldComment = "";
  @tracked oldDuration = moment.duration();

  get report() {
    return this.fetchReport.lastSuccessful?.value;
  }

  fetchReport = task(async () => {
    return await this.store.findRecord("report", this.args["report-id"][0]);
  });

  @action
  async showModal() {
    this.isModalVisible = true;
    const report = await this.fetchReport.perform();
    this.oldComment = report.comment ?? "";
    this.oldDuration = report.duration
      ? moment.duration(report.duration)
      : moment.duration();
    this.newDuration = moment.duration();
    this.newComment = "";
    this.newTask = null;
  }

  @action
  onSetNewTask(task) {
    this.newTask = task;
  }

  get remainingOldDuration() {
    const remaining = moment
      .duration(this.oldDuration)
      .subtract(this.newDuration);
    return remaining.asMilliseconds() > 0 ? remaining : moment.duration();
  }

  @action
  onNewDurationChange(val) {
    const totalMs = this.oldDuration.asMilliseconds();
    const ms = Math.min(val?.asMilliseconds() ?? 0, totalMs);
    this.newDuration = moment.duration(Math.max(ms, 0));
  }

  @action
  onRemainingDurationChange(val) {
    const totalMs = this.oldDuration.asMilliseconds();
    const remainingMs = Math.min(val?.asMilliseconds() ?? 0, totalMs);
    this.newDuration = moment.duration(Math.max(totalMs - remainingMs, 0));
  }

  get isValidSplit() {
    if (!this.newTask) return false;
    if (!this.newDuration || this.newDuration.asMilliseconds() <= 0) {
      return false;
    }
    if (
      !this.oldDuration ||
      this.newDuration.asMilliseconds() >= this.oldDuration.asMilliseconds()
    ) {
      return false;
    }
    return true;
  }

  splitReport = task(async () => {
    if (!this.isValidSplit) {
      this.notify.error(
        "Please select a task and enter a valid duration for the new report.",
      );
      return;
    }

    try {
      const report = this.report;

      report.comment = this.oldComment;
      report.duration = this.remainingOldDuration;
      await report.save();

      const newReport = this.store.createRecord("report", {
        date: report.date,
        user: report.user,
        task: this.newTask,
        comment: this.newComment,
        duration: this.newDuration,
        review: report.review,
        notBillable: report.notBillable,
      });
      await newReport.save();

      this.notify.success("Report split successfully.");
      this.isModalVisible = false;
      if (this.args.afterSave && typeof this.args.afterSave === "function") {
        this.args.afterSave();
      }
    } catch {
      this.notify.error("Could not split the report.");
    }
  });

  @action
  closeModal() {
    this.isModalVisible = false;
  }
}
